from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
import time
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


WRITE_BLOCK_MESSAGE = (
    "Phase 1 is read/query only. Write operations are blocked until explicit approvals "
    "and Phase 2 guardrails are enabled."
)


class ReadOnlyIntegrationHub:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def query(self, source: str, query: str, config: dict[str, Any] | None = None) -> str:
        source = source.lower()
        config = config or {}
        if source == "repo":
            return self._query_repo(query, config)
        if source == "notion":
            return self._query_notion(query, config)
        if source == "mcp":
            return self._query_mcp(query, config)
        if source == "notebooklm":
            return self._query_notebooklm(query, config)
        return f"Unknown source '{source}'."

    def write(self, source: str, operation: str, payload: dict) -> None:
        _ = (source, operation, payload)
        raise PermissionError(WRITE_BLOCK_MESSAGE)

    def validate_source(self, source: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
        source = source.lower().strip()
        config = config or {}
        if source == "repo":
            return self._validate_repo_source(config)
        if source == "notion":
            return self._validate_notion_source(config)
        if source == "mcp":
            return self._validate_mcp_source(config)
        if source == "notebooklm":
            return self._validate_notebooklm_source(config)
        return {
            "source": source,
            "ok": False,
            "checks": [],
            "errors": [f"Unknown source '{source}'."],
            "warnings": [],
            "details": {},
        }

    def _resolve_repo_root(self, config: dict[str, Any]) -> Path:
        root_value = str(config.get("root") or "").strip()
        if not root_value:
            return self.workspace_root
        candidate = Path(root_value)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError:
            return self.workspace_root
        return candidate

    def _query_repo(self, query: str, config: dict[str, Any]) -> str:
        search_root = self._resolve_repo_root(config)
        matches: list[str] = []
        needle = query.lower()
        for path in search_root.rglob("*"):
            if len(matches) >= 40:
                break
            if path.is_file() and needle in path.name.lower():
                matches.append(str(path.relative_to(self.workspace_root)))
        if not matches:
            return f"No repo filename matches found for '{query}'."
        heading = "Repo matches"
        if search_root != self.workspace_root:
            heading = f"Repo matches under {search_root.relative_to(self.workspace_root)}"
        return f"{heading}:\n" + "\n".join(f"- {item}" for item in matches)

    def _run_command(self, args: list[str], timeout_sec: float = 12.0) -> tuple[int, str, str]:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_sec,
            )
            return result.returncode, result.stdout or "", result.stderr or ""
        except PermissionError:
            try:
                command_line = subprocess.list2cmdline(args)
                result = subprocess.run(
                    ["cmd.exe", "/d", "/s", "/c", command_line],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_sec,
                )
                return result.returncode, result.stdout or "", result.stderr or ""
            except Exception as exc:
                return -1, "", f"Command failed: {exc}"
        except FileNotFoundError:
            return -1, "", f"Command not found: {args[0]}"
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout_sec:.1f}s: {' '.join(args)}"
        except Exception as exc:
            return -1, "", f"Command failed: {exc}"

    def _build_codex_mcp_command(self, subcommand_args: list[str]) -> list[str]:
        # Local override avoids failures when global config carries unsupported reasoning values.
        return ["codex", "-c", "model_reasoning_effort=high", "mcp", *subcommand_args]

    def _load_mcp_servers(self) -> tuple[list[dict[str, Any]], str | None]:
        code, stdout, stderr = self._run_command(self._build_codex_mcp_command(["list", "--json"]))
        if code != 0:
            reason = stderr.strip() or stdout.strip() or "codex mcp list failed"
            return [], reason
        try:
            parsed = json.loads(stdout or "[]")
        except json.JSONDecodeError:
            return [], "Unable to parse JSON from codex mcp list --json."
        if not isinstance(parsed, list):
            return [], "Unexpected JSON shape from codex mcp list --json."
        servers = [item for item in parsed if isinstance(item, dict)]
        return servers, None

    def _load_mcp_server_details(self, server: str) -> tuple[dict[str, Any], str | None]:
        code, stdout, stderr = self._run_command(
            self._build_codex_mcp_command(["get", server, "--json"])
        )
        if code != 0:
            reason = stderr.strip() or stdout.strip() or f"codex mcp get {server} failed"
            return {}, reason
        try:
            parsed = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            return {}, f"Unable to parse JSON from codex mcp get {server} --json."
        if not isinstance(parsed, dict):
            return {}, f"Unexpected JSON shape from codex mcp get {server} --json."
        return parsed, None

    def _find_mcp_server(self, servers: list[dict[str, Any]], server: str) -> dict[str, Any]:
        lowered = server.lower().strip()
        for item in servers:
            name = str(item.get("name") or "").lower().strip()
            if name == lowered:
                return item
        return {}

    def _parse_toolset(self, config: dict[str, Any]) -> list[str]:
        raw = str(config.get("toolset") or "").strip()
        if not raw:
            return []
        names = [piece.strip() for piece in raw.split(",")]
        return [name for name in names if name]

    def _mcp_query_profile(self, config: dict[str, Any]) -> dict[str, Any]:
        server = str(config.get("server") or "").lower().strip()
        profiles = config.get("profiles", {})
        if server and isinstance(profiles, dict):
            for profile_server, profile in profiles.items():
                if str(profile_server).lower().strip() == server and isinstance(profile, dict):
                    return profile

        profile = config.get("query_profile", {})
        if isinstance(profile, dict):
            return profile
        return {}

    def _available_mcp_tool_names(self, tools: list[dict[str, Any]]) -> str:
        names = [str(item.get("name") or "") for item in tools if isinstance(item, dict)]
        return ", ".join(name for name in names if name) or "(none)"

    def _choose_profiled_mcp_tool(self, tools: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(profile.get("tool") or "").strip()
        if not tool_name:
            raise RuntimeError("MCP query profile is missing tool.")
        lowered = tool_name.lower()
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if str(tool.get("name") or "").lower() == lowered:
                return tool
        available = self._available_mcp_tool_names(tools)
        raise RuntimeError(
            f"MCP query profile tool '{tool_name}' was not listed by server. "
            f"Available tools: {available}."
        )

    def _mcp_profile_argument_templates(self, profile: dict[str, Any]) -> dict[str, Any]:
        raw = profile.get("arguments")
        if isinstance(raw, dict) and raw:
            return raw
        raw = profile.get("argument_map")
        if isinstance(raw, dict):
            return raw
        return {}

    def _mcp_profile_uses_query(self, value: Any) -> bool:
        if isinstance(value, str):
            return "{query}" in value
        if isinstance(value, dict):
            return any(self._mcp_profile_uses_query(item) for item in value.values())
        if isinstance(value, list):
            return any(self._mcp_profile_uses_query(item) for item in value)
        return False

    def _render_mcp_profile_value(self, value: Any, query: str, config: dict[str, Any]) -> Any:
        if isinstance(value, str):
            replacements = {}
            for key, replacement in config.items():
                if isinstance(replacement, str | int | float | bool):
                    replacements[str(key)] = str(replacement)
            replacements["query"] = query
            rendered = value
            for key, replacement in replacements.items():
                rendered = rendered.replace(f"{{{key}}}", replacement)
            return rendered
        if isinstance(value, dict):
            return {
                str(key): self._render_mcp_profile_value(item, query, config)
                for key, item in value.items()
                if str(key).strip()
            }
        if isinstance(value, list):
            return [self._render_mcp_profile_value(item, query, config) for item in value]
        return value

    def _build_mcp_tool_arguments(
        self,
        profile: dict[str, Any],
        tool: dict[str, Any],
        query: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        raw_args = self._mcp_profile_argument_templates(profile)
        arguments = {
            str(key): self._render_mcp_profile_value(value, query, config)
            for key, value in raw_args.items()
            if str(key).strip()
        }
        query_arg = str(profile.get("query_arg") or "").strip()
        uses_query = self._mcp_profile_uses_query(raw_args)

        if not arguments:
            arg_key = query_arg or self._extract_query_arg_key(tool)
            if not arg_key:
                raise RuntimeError(
                    "MCP query profile must define arguments or query_arg, "
                    "or target a tool with a query-like string argument."
                )
            arguments[arg_key] = query
        elif not uses_query:
            arg_key = query_arg or self._extract_query_arg_key(tool)
            if not arg_key:
                raise RuntimeError(
                    "MCP query profile does not map the user query, "
                    "and no query-like tool argument could be inferred."
                )
            arguments.setdefault(arg_key, query)
        return arguments

    def _extract_query_arg_key(self, tool: dict[str, Any]) -> str | None:
        schema = tool.get("inputSchema", {})
        if not isinstance(schema, dict):
            return None
        props = schema.get("properties", {})
        if not isinstance(props, dict):
            return None

        prioritized = ["query", "q", "search", "text", "prompt", "question", "term", "keywords"]
        for key in prioritized:
            descriptor = props.get(key)
            if isinstance(descriptor, dict):
                prop_type = descriptor.get("type")
                if prop_type in {None, "string"}:
                    return key

        query_hints = ("query", "search", "find", "lookup", "question", "keyword", "term")
        for key, descriptor in props.items():
            if not isinstance(key, str) or not isinstance(descriptor, dict):
                continue
            if descriptor.get("type") not in {None, "string"}:
                continue
            lowered_key = key.lower()
            if any(hint in lowered_key for hint in query_hints):
                return key
            description = str(descriptor.get("description") or "").lower()
            if any(hint in description for hint in query_hints):
                return key
        return None

    def _rank_tool_for_query(self, tool: dict[str, Any], preferred_names: list[str]) -> int:
        name = str(tool.get("name") or "").lower()
        if not name:
            return -1
        score = 0
        if self._extract_query_arg_key(tool):
            score += 10
        if any(pref.lower() == name for pref in preferred_names):
            score += 100
        for token, bonus in (
            ("query", 6),
            ("search", 6),
            ("find", 5),
            ("lookup", 5),
            ("read", 3),
            ("list", 1),
        ):
            if token in name:
                score += bonus
        return score

    def _choose_semantic_tool(self, tools: list[dict[str, Any]], preferred_names: list[str]) -> dict[str, Any]:
        ranked = sorted(
            (tool for tool in tools if isinstance(tool, dict)),
            key=lambda item: self._rank_tool_for_query(item, preferred_names),
            reverse=True,
        )
        if not ranked:
            return {}
        best = ranked[0]
        if self._rank_tool_for_query(best, preferred_names) <= 0:
            return {}
        if not self._extract_query_arg_key(best):
            return {}
        return best

    def _format_tool_output(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return "Tool returned non-object payload."
        content = payload.get("content", [])
        lines: list[str] = []
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    lines.append(str(item))
                    continue
                if item.get("type") == "text":
                    text = str(item.get("text") or "").strip()
                    if text:
                        lines.append(text)
                    continue
                lines.append(json.dumps(item, ensure_ascii=False))
        structured = payload.get("structuredContent")
        if structured is not None:
            lines.append(json.dumps(structured, ensure_ascii=False, indent=2))
        output = "\n".join(line for line in lines if line)
        return output.strip() or "Tool returned no textual content."

    def _read_json_message(self, queue: Queue[dict[str, Any]], request_id: int, timeout_sec: float) -> dict[str, Any]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            try:
                item = queue.get(timeout=remaining)
            except Empty as exc:
                raise RuntimeError("Timed out waiting for MCP server response.") from exc
            if not isinstance(item, dict):
                continue
            if item.get("id") != request_id:
                continue
            if "error" in item:
                error_obj = item.get("error")
                if isinstance(error_obj, dict):
                    message = str(error_obj.get("message") or "Unknown MCP error.")
                else:
                    message = str(error_obj or "Unknown MCP error.")
                raise RuntimeError(message)
            result = item.get("result")
            if isinstance(result, dict):
                return result
            return {}
        raise RuntimeError("Timed out waiting for MCP server response.")

    def _query_mcp_via_stdio(self, query: str, config: dict[str, Any], transport: dict[str, Any]) -> str:
        command = str(transport.get("command") or "").strip()
        if not command:
            raise RuntimeError("MCP stdio transport is missing command.")
        args = transport.get("args", [])
        argv = [command]
        if isinstance(args, list):
            argv.extend(str(piece) for piece in args)

        env = os.environ.copy()
        config_env = transport.get("env", {})
        if isinstance(config_env, dict):
            for key, value in config_env.items():
                if isinstance(key, str) and value is not None:
                    env[key] = str(value)

        raw_cwd = transport.get("cwd")
        cwd = str(raw_cwd).strip() if isinstance(raw_cwd, str) else None
        if not cwd:
            cwd = None

        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=cwd,
        )
        queue: Queue[dict[str, Any]] = Queue()

        def reader() -> None:
            stream = proc.stdout
            if stream is None:
                return
            for raw in stream:
                line = raw.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    queue.put(parsed)

        thread = Thread(target=reader, daemon=True)
        thread.start()

        def send(message: dict[str, Any]) -> None:
            stream = proc.stdin
            if stream is None:
                raise RuntimeError("MCP stdio stdin is unavailable.")
            stream.write(json.dumps(message) + "\n")
            stream.flush()

        try:
            initialize_id = 1
            send(
                {
                    "jsonrpc": "2.0",
                    "id": initialize_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "novel-agent-cockpit",
                            "version": "0.1.0",
                        },
                    },
                }
            )
            self._read_json_message(queue, initialize_id, timeout_sec=12.0)
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            )

            list_id = 2
            send(
                {
                    "jsonrpc": "2.0",
                    "id": list_id,
                    "method": "tools/list",
                    "params": {},
                }
            )
            list_result = self._read_json_message(queue, list_id, timeout_sec=12.0)
            raw_tools = list_result.get("tools", [])
            tools = raw_tools if isinstance(raw_tools, list) else []
            profile = self._mcp_query_profile(config)
            if profile:
                chosen = self._choose_profiled_mcp_tool(tools, profile)
                call_arguments = self._build_mcp_tool_arguments(profile, chosen, query, config)
            else:
                preferred = self._parse_toolset(config)
                chosen = self._choose_semantic_tool(tools, preferred)
                if not chosen:
                    available = self._available_mcp_tool_names(tools)
                    preferred_text = ", ".join(preferred) if preferred else "(not set)"
                    raise RuntimeError(
                        "No MCP tool could be selected for semantic query. "
                        f"Preferred toolset: {preferred_text}. Available tools: {available}."
                    )

                arg_key = self._extract_query_arg_key(chosen)
                if not arg_key:
                    raise RuntimeError("Chosen MCP tool does not expose a query-like string argument.")
                call_arguments = {arg_key: query}

            call_id = 3
            send(
                {
                    "jsonrpc": "2.0",
                    "id": call_id,
                    "method": "tools/call",
                    "params": {
                        "name": str(chosen.get("name") or ""),
                        "arguments": call_arguments,
                    },
                }
            )
            call_result = self._read_json_message(queue, call_id, timeout_sec=20.0)
            if call_result.get("isError"):
                raise RuntimeError(self._format_tool_output(call_result))

            rendered = self._format_tool_output(call_result)
            return f"MCP tool '{chosen.get('name')}' result:\n{rendered}"
        finally:
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass

    def _notion_search(self, query: str, notion_token: str) -> dict[str, Any]:
        payload = {"query": query, "page_size": 10}
        body = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            url="https://api.notion.com/v1/search",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib_request.urlopen(req, timeout=12.0) as response:
                raw = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:300]
            raise RuntimeError(f"Notion HTTP {exc.code}: {detail}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"Notion connection failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("Notion request timed out.") from exc

        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Notion returned malformed JSON.") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Notion returned an unexpected payload shape.")
        return parsed

    def _notion_result_title(self, item: dict[str, Any]) -> str:
        if not isinstance(item, dict):
            return "Untitled"
        obj_type = str(item.get("object") or "")
        if obj_type == "database":
            title_parts = item.get("title", [])
            if isinstance(title_parts, list):
                value = "".join(str(chunk.get("plain_text") or "") for chunk in title_parts if isinstance(chunk, dict))
                if value.strip():
                    return value.strip()
        if obj_type == "page":
            properties = item.get("properties", {})
            if isinstance(properties, dict):
                for prop in properties.values():
                    if not isinstance(prop, dict):
                        continue
                    if prop.get("type") != "title":
                        continue
                    title_items = prop.get("title", [])
                    if isinstance(title_items, list):
                        value = "".join(
                            str(chunk.get("plain_text") or "")
                            for chunk in title_items
                            if isinstance(chunk, dict)
                        )
                        if value.strip():
                            return value.strip()
        return str(item.get("id") or "Untitled")

    def _source_hint(self, source: str, config: dict[str, Any]) -> str:
        if not config:
            return ""
        keys = ", ".join(sorted(config.keys())[:6])
        if keys:
            return f" Configured keys: {keys}."
        return f" {source.title()} source is configured."

    def _apply_required_field_check(
        self,
        source: str,
        config: dict[str, Any],
        required_fields: list[str],
        checks: list[dict[str, str]],
        errors: list[str],
    ) -> None:
        missing = [field for field in required_fields if not str(config.get(field) or "").strip()]
        if missing:
            checks.append(
                {
                    "name": "required_fields",
                    "status": "fail",
                    "message": "Missing required fields: " + ", ".join(missing),
                }
            )
            errors.extend(f"{source}.{field} is required." for field in missing)
            return
        checks.append(
            {
                "name": "required_fields",
                "status": "pass",
                "message": "Required fields are present.",
            }
        )

    def _validate_repo_source(self, config: dict[str, Any]) -> dict[str, Any]:
        checks: list[dict[str, str]] = []
        errors: list[str] = []
        warnings: list[str] = []
        self._apply_required_field_check("repo", config, ["root"], checks, errors)

        root_value = str(config.get("root") or "").strip()
        resolved_root = self.workspace_root
        if root_value:
            candidate = Path(root_value)
            if not candidate.is_absolute():
                candidate = self.workspace_root / candidate
            resolved_root = candidate.resolve()

            try:
                resolved_root.relative_to(self.workspace_root)
            except ValueError:
                checks.append(
                    {
                        "name": "root_inside_workspace",
                        "status": "fail",
                        "message": "Configured root resolves outside workspace root.",
                    }
                )
                errors.append("repo.root must resolve inside the workspace root.")
            else:
                checks.append(
                    {
                        "name": "root_inside_workspace",
                        "status": "pass",
                        "message": "Configured root resolves inside workspace root.",
                    }
                )

            if not resolved_root.exists():
                checks.append(
                    {
                        "name": "root_exists",
                        "status": "fail",
                        "message": "Configured root does not exist yet.",
                    }
                )
                errors.append(f"repo.root does not exist: {root_value}")
            elif not resolved_root.is_dir():
                checks.append(
                    {
                        "name": "root_exists",
                        "status": "fail",
                        "message": "Configured root points to a file, not a directory.",
                    }
                )
                errors.append(f"repo.root must be a directory: {root_value}")
            else:
                checks.append(
                    {
                        "name": "root_exists",
                        "status": "pass",
                        "message": "Configured root directory exists.",
                    }
                )
        else:
            checks.append(
                {
                    "name": "root_exists",
                    "status": "fail",
                    "message": "Cannot validate root until repo.root is configured.",
                }
            )

        return {
            "source": "repo",
            "ok": not errors,
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
            "details": {
                "workspace_root": str(self.workspace_root),
                "configured_root": root_value,
                "resolved_root": str(resolved_root),
            },
        }

    def _validate_notion_source(self, config: dict[str, Any]) -> dict[str, Any]:
        checks: list[dict[str, str]] = []
        errors: list[str] = []
        warnings: list[str] = []
        self._apply_required_field_check("notion", config, ["workspace_id", "token_env"], checks, errors)

        token_env = str(config.get("token_env") or "").strip()
        if token_env:
            if os.getenv(token_env):
                checks.append(
                    {
                        "name": "token_env_present",
                        "status": "pass",
                        "message": f"Environment variable '{token_env}' is set.",
                    }
                )
            else:
                checks.append(
                    {
                        "name": "token_env_present",
                        "status": "fail",
                        "message": f"Environment variable '{token_env}' is not set.",
                    }
                )
                errors.append(f"notion.token_env points to missing environment variable: {token_env}")

        return {
            "source": "notion",
            "ok": not errors,
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
            "details": {
                "workspace_id": str(config.get("workspace_id") or ""),
                "token_env": token_env,
            },
        }

    def _validate_mcp_source(self, config: dict[str, Any]) -> dict[str, Any]:
        checks: list[dict[str, str]] = []
        errors: list[str] = []
        warnings: list[str] = []
        self._apply_required_field_check("mcp", config, ["server"], checks, errors)
        profile = self._mcp_query_profile(config)
        profiled_tool = str(profile.get("tool") or "").strip() if profile else ""
        if profile:
            checks.append(
                {
                    "name": "query_profile",
                    "status": "pass" if profiled_tool else "fail",
                    "message": (
                        f"MCP query profile will call tool '{profiled_tool}'."
                        if profiled_tool
                        else "MCP query profile is missing a tool name."
                    ),
                }
            )
            if not profiled_tool:
                errors.append("mcp query profile is missing tool.")
        server = str(config.get("server") or "").strip()
        if server:
            servers, list_error = self._load_mcp_servers()
            if list_error:
                checks.append(
                    {
                        "name": "codex_mcp_list",
                        "status": "warn",
                        "message": f"Unable to inspect local MCP server list: {list_error}",
                    }
                )
                warnings.append(f"Could not verify MCP server existence: {list_error}")
            else:
                found = self._find_mcp_server(servers, server)
                if not found:
                    checks.append(
                        {
                            "name": "server_registered",
                            "status": "fail",
                            "message": f"Configured server '{server}' is not registered locally.",
                        }
                    )
                    errors.append(f"mcp.server is not registered locally: {server}")
                else:
                    checks.append(
                        {
                            "name": "server_registered",
                            "status": "pass",
                            "message": f"Configured server '{server}' is registered locally.",
                        }
                    )
                    transport = found.get("transport", {})
                    if isinstance(transport, dict):
                        transport_type = str(transport.get("type") or "unknown")
                        if transport_type == "stdio":
                            checks.append(
                                {
                                    "name": "semantic_query_support",
                                    "status": "pass",
                                    "message": "Server transport supports MVP semantic tool queries (stdio).",
                                }
                            )
                        else:
                            checks.append(
                                {
                                    "name": "semantic_query_support",
                                    "status": "warn",
                                    "message": f"Semantic MCP query currently supports stdio only (found {transport_type}).",
                                }
                            )
                            warnings.append(
                                f"Configured server transport '{transport_type}' currently uses metadata fallback for queries."
                            )
        return {
            "source": "mcp",
            "ok": not errors,
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
            "details": {
                "server": str(config.get("server") or ""),
                "workspace": str(config.get("workspace") or ""),
                "toolset": str(config.get("toolset") or ""),
                "profiled_tool": profiled_tool,
            },
        }

    def _validate_notebooklm_source(self, config: dict[str, Any]) -> dict[str, Any]:
        checks: list[dict[str, str]] = []
        errors: list[str] = []
        warnings: list[str] = []
        self._apply_required_field_check("notebooklm", config, ["notebook_id"], checks, errors)
        mcp_config = self._notebooklm_mcp_config(config)
        mcp_validation: dict[str, Any] | None = None
        if mcp_config:
            checks.append(
                {
                    "name": "mcp_adapter_configured",
                    "status": "pass",
                    "message": f"NotebookLM will query MCP server '{mcp_config['server']}'.",
                }
            )
            mcp_validation = self._validate_mcp_source(mcp_config)
            if not mcp_validation.get("ok"):
                for error in mcp_validation.get("errors", []):
                    errors.append(f"NotebookLM MCP adapter: {error}")
            warnings.extend(
                f"NotebookLM MCP adapter: {warning}"
                for warning in mcp_validation.get("warnings", [])
            )
        else:
            warnings.append("NotebookLM live connector checks are not wired without an MCP adapter.")
            checks.append(
                {
                    "name": "live_handshake",
                    "status": "warn",
                    "message": "Configure notebooklm.mcp_server to enable live NotebookLM queries.",
                }
            )
        return {
            "source": "notebooklm",
            "ok": not errors,
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
            "details": {
                "notebook_id": str(config.get("notebook_id") or ""),
                "project": str(config.get("project") or ""),
                "mcp_server": str(config.get("mcp_server") or config.get("server") or ""),
                "mcp_validation": mcp_validation,
            },
        }

    def _query_notion(self, query: str, config: dict[str, Any]) -> str:
        workspace_id = str(config.get("workspace_id") or "").strip()
        token_env = str(config.get("token_env") or "").strip()
        if not workspace_id or not token_env:
            return (
                "Notion query requires notion.workspace_id and notion.token_env in source config. "
                f"{self._source_hint('notion', config)}"
            )
        token = os.getenv(token_env)
        if not token:
            return f"Notion token environment variable '{token_env}' is not set."

        try:
            payload = self._notion_search(query, token)
        except RuntimeError as exc:
            return f"Notion query failed: {exc}"

        raw_results = payload.get("results", [])
        results = raw_results if isinstance(raw_results, list) else []
        if not results:
            return f'No Notion results found for "{query}".'

        lines = [f'Notion results for "{query}" ({min(len(results), 8)} shown):']
        for item in results[:8]:
            if not isinstance(item, dict):
                continue
            obj_type = str(item.get("object") or "item")
            title = self._notion_result_title(item)
            url = str(item.get("url") or "").strip()
            if url:
                lines.append(f"- [{obj_type}] {title} -> {url}")
            else:
                lines.append(f"- [{obj_type}] {title}")
        if payload.get("has_more"):
            lines.append("- More results are available in Notion.")
        return "\n".join(lines)

    def _query_mcp(self, query: str, config: dict[str, Any]) -> str:
        server = str(config.get("server") or "").strip()
        if not server:
            return "MCP query requires mcp.server in source config."

        servers, list_error = self._load_mcp_servers()
        if list_error:
            return f"MCP query failed to inspect local Codex MCP servers: {list_error}"

        found = self._find_mcp_server(servers, server)
        if not found:
            available = sorted(str(item.get('name') or "").strip() for item in servers if item.get("name"))
            available_text = ", ".join(item for item in available if item)
            if available_text:
                return f"MCP server '{server}' was not found. Available configured servers: {available_text}"
            return f"MCP server '{server}' was not found and no configured servers were returned."

        detail, detail_error = self._load_mcp_server_details(server)
        info = detail if detail else found
        transport = info.get("transport", {})
        transport_type = "unknown"
        if isinstance(transport, dict):
            transport_type = str(transport.get("type") or "unknown")
        auth_status = str(info.get("auth_status") or found.get("auth_status") or "unknown")
        enabled = bool(info.get("enabled", found.get("enabled", True)))

        lines = [
            f'MCP server "{server}" is configured.',
            f"- enabled: {enabled}",
            f"- transport: {transport_type}",
            f"- auth: {auth_status}",
        ]
        if detail_error:
            lines.append(f"- detail warning: {detail_error}")
        if isinstance(transport, dict):
            url = str(transport.get("url") or "").strip()
            command = str(transport.get("command") or "").strip()
            if url:
                lines.append(f"- url: {url}")
            if command:
                lines.append(f"- command: {command}")
            if transport_type == "stdio":
                try:
                    semantic = self._query_mcp_via_stdio(query, config, transport)
                except RuntimeError as exc:
                    lines.append(f'- query intent: "{query}"')
                    lines.append(f"- semantic query error: {exc}")
                    return "\n".join(lines)
                lines.append(f'- query intent: "{query}"')
                lines.append(semantic)
                return "\n".join(lines)
        lines.append(f'- query intent: "{query}"')
        lines.append("- semantic query currently supports stdio transport only; metadata fallback shown.")
        return "\n".join(lines)

    def _query_notebooklm(self, query: str, config: dict[str, Any]) -> str:
        mcp_config = self._notebooklm_mcp_config(config)
        if not mcp_config:
            return (
                f'NotebookLM query placeholder for "{query}". '
                "Configure notebooklm.mcp_server and a query profile to enable live NotebookLM MCP queries."
                f"{self._source_hint('notebooklm', config)}"
            )
        notebook_id = str(config.get("notebook_id") or "").strip()
        result = self._query_mcp(query, mcp_config)
        return f'NotebookLM MCP query for notebook "{notebook_id}":\n{result}'

    def _notebooklm_mcp_config(self, config: dict[str, Any]) -> dict[str, Any]:
        server = str(config.get("mcp_server") or config.get("server") or "").strip()
        if not server:
            return {}
        mcp_config: dict[str, Any] = {
            "server": server,
            "workspace": str(config.get("project") or config.get("notebook_id") or ""),
            "notebook_id": str(config.get("notebook_id") or ""),
            "project": str(config.get("project") or ""),
        }
        for key in ("toolset", "query_profile", "profiles"):
            value = config.get(key)
            if value:
                mcp_config[key] = value
        return mcp_config

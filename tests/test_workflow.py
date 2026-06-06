from __future__ import annotations

import asyncio
import json
import time

from fastapi.testclient import TestClient

from app.integrations import ReadOnlyIntegrationHub
from tests.conftest import wait_for_status


def create_chapter_with_beats(client: TestClient, beat_count: int = 4) -> int:
    chapter = client.post(
        "/chapters",
        json={"title": "Chapter One", "canonical_path": "manuscript/ch01.md"},
    ).json()
    beats = [f"Beat {i}: pressure escalates {i}" for i in range(1, beat_count + 1)]
    client.post(f"/chapters/{chapter['id']}/beats", json={"beats": beats})
    client.post(f"/chapters/{chapter['id']}/lock-spine")
    return int(chapter["id"])


def test_spine_lock_loop_generates_ordered_todos(make_client) -> None:
    client, _ = make_client("spine.db")
    chapter_id = create_chapter_with_beats(client, beat_count=18)

    run = client.post("/runs", json={"chapter_id": chapter_id, "mode": "section"}).json()
    todos = client.get(f"/runs/{run['id']}/todos").json()

    assert len(todos) == 35
    assert todos[0]["task_type"] == "draft_scene"
    assert todos[0]["beat_id"] < todos[-1]["beat_id"]
    assert any(todo["task_type"] == "polish_transition" for todo in todos)


def test_fast_run_creation_is_non_blocking(make_client) -> None:
    client, _ = make_client("nonblocking.db")
    chapter_id = create_chapter_with_beats(client, beat_count=40)

    started = time.perf_counter()
    run = client.post("/runs", json={"chapter_id": chapter_id, "mode": "fast"}).json()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.45
    assert run["status"] in {"queued", "in_progress", "waiting_approval", "completed"}


def test_section_mode_revise_then_approve_only_promotes_latest(make_client) -> None:
    client, _ = make_client("section.db")
    chapter_id = create_chapter_with_beats(client, beat_count=2)

    run = client.post("/runs", json={"chapter_id": chapter_id, "mode": "section"}).json()
    run_id = int(run["id"])
    wait_for_status(client, run_id, {"waiting_approval"})

    client.post(f"/runs/{run_id}/actions", json={"action": "revise", "notes": "note-A"})
    client.post(f"/runs/{run_id}/actions", json={"action": "revise", "notes": "note-B"})
    client.post(f"/runs/{run_id}/actions", json={"action": "approve"})

    preview = client.get(f"/chapters/{chapter_id}/preview").json()["preview"]
    assert "note-B" in preview
    assert "note-A" not in preview


def test_fast_mode_completes_with_checkpoint_and_replay_log(make_client) -> None:
    client, _ = make_client("fast.db")
    chapter_id = create_chapter_with_beats(client, beat_count=3)

    run = client.post("/runs", json={"chapter_id": chapter_id, "mode": "fast"}).json()
    run_id = int(run["id"])
    final = wait_for_status(client, run_id, {"completed", "failed"})
    assert final["status"] == "completed"

    checkpoint = final["checkpoint_json"]
    assert checkpoint["completed_todos"] == checkpoint["total_todos"]

    events = client.get(f"/runs/{run_id}/events/history").json()
    event_types = [item["event_type"] for item in events]
    assert "run_created" in event_types
    assert "run_completed" in event_types
    assert "fragment_approved" in event_types


def test_pause_resume_recovery_across_restart(make_client) -> None:
    client_a, settings = make_client("recovery.db")
    chapter_id = create_chapter_with_beats(client_a, beat_count=3)
    run = client_a.post("/runs", json={"chapter_id": chapter_id, "mode": "section"}).json()
    run_id = int(run["id"])
    wait_for_status(client_a, run_id, {"waiting_approval"})

    pause = client_a.post(f"/runs/{run_id}/actions", json={"action": "pause"}).json()
    assert pause["status"] == "paused"
    client_a.close()

    from app.api import create_app

    app_b = create_app(settings)
    client_b = TestClient(app_b)
    try:
        resumed = client_b.post(f"/runs/{run_id}/actions", json={"action": "resume"}).json()
        assert resumed["status"] in {"waiting_approval", "in_progress"}
        client_b.post(f"/runs/{run_id}/actions", json={"action": "approve"})
        final = wait_for_status(client_b, run_id, {"waiting_approval", "completed"})
        assert final["status"] in {"waiting_approval", "completed"}
    finally:
        client_b.close()


def test_section_pause_during_generation_does_not_get_overwritten(make_client) -> None:
    client, _ = make_client("pause_race.db")
    provider = client.app.state.provider
    original_draft_scene = provider.draft_scene

    async def slow_draft_scene(beat_summary: str, chapter_title: str) -> str:
        await asyncio.sleep(0.12)
        return await original_draft_scene(beat_summary, chapter_title)

    provider.draft_scene = slow_draft_scene  # type: ignore[assignment]

    chapter_id = create_chapter_with_beats(client, beat_count=1)
    run = client.post("/runs", json={"chapter_id": chapter_id, "mode": "section"}).json()
    run_id = int(run["id"])

    latest = {}
    for _ in range(60):
        latest = client.get(f"/runs/{run_id}").json()
        if latest.get("status") == "in_progress":
            break
        time.sleep(0.01)
    assert latest.get("status") == "in_progress"

    paused = client.post(f"/runs/{run_id}/actions", json={"action": "pause"}).json()
    assert paused["status"] == "paused"

    time.sleep(0.2)
    after_generation = client.get(f"/runs/{run_id}").json()
    assert after_generation["status"] == "paused"

    todos = client.get(f"/runs/{run_id}/todos").json()
    assert todos[0]["status"] in {"pending", "waiting_approval"}

    resumed = client.post(f"/runs/{run_id}/actions", json={"action": "resume"}).json()
    assert resumed["status"] in {"in_progress", "waiting_approval"}

    final = wait_for_status(client, run_id, {"in_progress", "waiting_approval", "completed"})
    if final["status"] == "waiting_approval":
        client.post(f"/runs/{run_id}/actions", json={"action": "approve"})
        final = wait_for_status(client, run_id, {"completed"})
    assert final["status"] == "completed"


def test_upload_manuscript_and_link_existing_file(make_client) -> None:
    client, settings = make_client("upload.db")
    uploaded = client.post(
        "/manuscript/upload",
        json={
            "filename": "ch01.md",
            "content": "# Chapter 1\n\nDraft body.\n",
            "chapter_title": "Chapter One",
            "overwrite": False,
        },
    ).json()
    assert uploaded["canonical_path"] == "manuscript/ch01.md"
    chapter_id = int(uploaded["chapter"]["id"])

    target = settings.workspace_root / uploaded["canonical_path"]
    assert target.exists()
    assert "Draft body" in target.read_text(encoding="utf-8")

    files = client.get("/manuscript/files").json()["files"]
    assert any(item["path"] == "manuscript/ch01.md" for item in files)

    linked = client.post(
        "/chapters/link-manuscript",
        json={"canonical_path": "manuscript/ch01.md", "chapter_title": "Chapter One Revised"},
    ).json()
    assert linked["linked"] == "existing"
    assert int(linked["chapter"]["id"]) == chapter_id
    assert linked["chapter"]["title"] == "Chapter One Revised"


def test_publish_chapter_writes_preview_to_canonical_file(make_client) -> None:
    client, settings = make_client("publish.db")
    chapter = client.post(
        "/chapters",
        json={"title": "Publish Test", "canonical_path": "manuscript/publish-test.md"},
    ).json()
    chapter_id = int(chapter["id"])
    client.post(
        f"/chapters/{chapter_id}/beats",
        json={"beats": ["Beat 1: hook", "Beat 2: escalation"]},
    )
    client.post(f"/chapters/{chapter_id}/lock-spine")

    run = client.post("/runs", json={"chapter_id": chapter_id, "mode": "fast"}).json()
    wait_for_status(client, int(run["id"]), {"completed"})
    preview = client.get(f"/chapters/{chapter_id}/preview").json()["preview"]
    assert "Beat 1: hook" in preview

    published = client.post(f"/chapters/{chapter_id}/publish", json={"overwrite": True}).json()
    target = settings.workspace_root / published["canonical_path"]
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert preview in content


def test_publish_chapter_dry_run_returns_diff_without_writing(make_client) -> None:
    client, settings = make_client("publish_dry_run.db")
    chapter = client.post(
        "/chapters",
        json={"title": "Dry Run Test", "canonical_path": "manuscript/dry-run.md"},
    ).json()
    chapter_id = int(chapter["id"])
    target = settings.workspace_root / "manuscript" / "dry-run.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Original draft\n", encoding="utf-8")
    client.post(f"/chapters/{chapter_id}/beats", json={"beats": ["Beat 1: hook"]})
    client.post(f"/chapters/{chapter_id}/lock-spine")
    run = client.post("/runs", json={"chapter_id": chapter_id, "mode": "fast"}).json()
    wait_for_status(client, int(run["id"]), {"completed"})

    previewed = client.post(
        f"/chapters/{chapter_id}/publish",
        json={"dry_run": True, "overwrite": False},
    ).json()

    assert previewed["status"] == "preview"
    assert previewed["bytes_written"] == 0
    assert previewed["would_write_bytes"] > 0
    assert previewed["target_exists"] is True
    assert previewed["backup_path"] is None
    assert "-Original draft" in previewed["diff"]
    assert "Beat 1: hook" in previewed["diff"]
    assert target.read_text(encoding="utf-8") == "Original draft\n"


def test_publish_chapter_creates_backup_before_overwrite(make_client) -> None:
    client, settings = make_client("publish_backup.db")
    chapter = client.post(
        "/chapters",
        json={"title": "Backup Test", "canonical_path": "manuscript/backup.md"},
    ).json()
    chapter_id = int(chapter["id"])
    target = settings.workspace_root / "manuscript" / "backup.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Original draft\n", encoding="utf-8")
    client.post(f"/chapters/{chapter_id}/beats", json={"beats": ["Beat 1: hook"]})
    client.post(f"/chapters/{chapter_id}/lock-spine")
    run = client.post("/runs", json={"chapter_id": chapter_id, "mode": "fast"}).json()
    wait_for_status(client, int(run["id"]), {"completed"})

    published = client.post(
        f"/chapters/{chapter_id}/publish",
        json={"overwrite": True, "backup": True},
    ).json()

    assert published["status"] == "published"
    assert published["target_exists"] is True
    assert published["backup_path"]
    backup = settings.workspace_root / published["backup_path"]
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "Original draft\n"
    assert "Beat 1: hook" in target.read_text(encoding="utf-8")
    assert "-Original draft" in published["diff"]


def test_integration_config_upload_is_runtime_configurable(make_client) -> None:
    client, _ = make_client("source_config.db")
    config_payload = {
        "sources": {
            "repo": {"root": "manuscript"},
            "mcp": {"server": "local-mcp", "workspace": "novel"},
        }
    }
    uploaded = client.post(
        "/integrations/sources/upload",
        json={"filename": "sources.json", "content": json.dumps(config_payload)},
    ).json()
    assert "repo" in uploaded["updated_sources"]
    assert "mcp" in uploaded["updated_sources"]

    listed = client.get("/integrations/sources").json()["sources"]
    assert listed["repo"]["root"] == "manuscript"
    assert listed["mcp"]["server"] == "local-mcp"

    queried = client.post("/integrations/query", json={"source": "mcp", "query": "chapter context"}).json()
    assert queried["config"]["server"] == "local-mcp"


def test_integration_notion_query_uses_live_adapter_path(make_client, monkeypatch) -> None:
    client, _ = make_client("notion_query.db")
    client.put(
        "/integrations/sources/notion",
        json={"config": {"workspace_id": "workspace-1", "token_env": "NOTION_TOKEN"}},
    )
    monkeypatch.setenv("NOTION_TOKEN", "test-notion-token")

    def fake_notion_search(query: str, notion_token: str) -> dict:
        assert query == "scene ideas"
        assert notion_token == "test-notion-token"
        return {
            "results": [
                {
                    "object": "page",
                    "id": "page-1",
                    "url": "https://notion.so/page-1",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Scene Ideas Board"}],
                        }
                    },
                }
            ]
        }

    monkeypatch.setattr(client.app.state.integrations, "_notion_search", fake_notion_search)

    queried = client.post("/integrations/query", json={"source": "notion", "query": "scene ideas"}).json()
    result = queried["result"]
    assert "Scene Ideas Board" in result
    assert "https://notion.so/page-1" in result


def test_integration_mcp_query_reports_real_server_metadata(make_client, monkeypatch) -> None:
    client, _ = make_client("mcp_query.db")
    client.put(
        "/integrations/sources/mcp",
        json={"config": {"server": "story-mcp", "workspace": "novel"}},
    )

    monkeypatch.setattr(
        client.app.state.integrations,
        "_load_mcp_servers",
        lambda: (
            [
                {
                    "name": "story-mcp",
                    "enabled": True,
                    "auth_status": "unsupported",
                    "transport": {"type": "streamable_http", "url": "https://example.test/mcp"},
                }
            ],
            None,
        ),
    )
    monkeypatch.setattr(
        client.app.state.integrations,
        "_load_mcp_server_details",
        lambda server: (
            {
                "name": server,
                "enabled": True,
                "auth_status": "unsupported",
                "transport": {"type": "streamable_http", "url": "https://example.test/mcp"},
            },
            None,
        ),
    )

    queried = client.post("/integrations/query", json={"source": "mcp", "query": "chapter context"}).json()
    result = queried["result"]
    assert 'MCP server "story-mcp" is configured.' in result
    assert "transport: streamable_http" in result
    assert 'query intent: "chapter context"' in result


def test_integration_mcp_query_executes_semantic_stdio_tool_path(make_client, monkeypatch) -> None:
    client, _ = make_client("mcp_query_stdio.db")
    client.put(
        "/integrations/sources/mcp",
        json={"config": {"server": "story-mcp", "toolset": "search_docs"}},
    )

    monkeypatch.setattr(
        client.app.state.integrations,
        "_load_mcp_servers",
        lambda: (
            [
                {
                    "name": "story-mcp",
                    "enabled": True,
                    "auth_status": "unsupported",
                    "transport": {"type": "stdio", "command": "story-mcp.exe", "args": []},
                }
            ],
            None,
        ),
    )
    monkeypatch.setattr(
        client.app.state.integrations,
        "_load_mcp_server_details",
        lambda server: (
            {
                "name": server,
                "enabled": True,
                "auth_status": "unsupported",
                "transport": {"type": "stdio", "command": "story-mcp.exe", "args": []},
            },
            None,
        ),
    )
    monkeypatch.setattr(
        client.app.state.integrations,
        "_query_mcp_via_stdio",
        lambda query, config, transport: "MCP tool 'search_docs' result:\nScene references...",
    )

    queried = client.post("/integrations/query", json={"source": "mcp", "query": "chapter context"}).json()
    result = queried["result"]
    assert "transport: stdio" in result
    assert "MCP tool 'search_docs' result:" in result
    assert "Scene references..." in result


def test_integration_mcp_query_reports_semantic_stdio_error(make_client, monkeypatch) -> None:
    client, _ = make_client("mcp_query_stdio_error.db")
    client.put(
        "/integrations/sources/mcp",
        json={"config": {"server": "story-mcp"}},
    )

    monkeypatch.setattr(
        client.app.state.integrations,
        "_load_mcp_servers",
        lambda: (
            [
                {
                    "name": "story-mcp",
                    "enabled": True,
                    "auth_status": "unsupported",
                    "transport": {"type": "stdio", "command": "story-mcp.exe", "args": []},
                }
            ],
            None,
        ),
    )
    monkeypatch.setattr(
        client.app.state.integrations,
        "_load_mcp_server_details",
        lambda server: (
            {
                "name": server,
                "enabled": True,
                "auth_status": "unsupported",
                "transport": {"type": "stdio", "command": "story-mcp.exe", "args": []},
            },
            None,
        ),
    )

    def fail_semantic(query: str, config: dict, transport: dict) -> str:
        raise RuntimeError("No suitable query tool was found.")

    monkeypatch.setattr(client.app.state.integrations, "_query_mcp_via_stdio", fail_semantic)

    queried = client.post("/integrations/query", json={"source": "mcp", "query": "chapter context"}).json()
    result = queried["result"]
    assert "semantic query error: No suitable query tool was found." in result


def test_mcp_query_profile_builds_explicit_tool_call_arguments(tmp_path) -> None:
    hub = ReadOnlyIntegrationHub(tmp_path)
    config = {
        "server": "story-mcp",
        "workspace": "novel",
        "profiles": {
            "story-mcp": {
                "tool": "search_docs",
                "arguments": {
                    "query": "{query}",
                    "scope": "{workspace}",
                    "limit": 5,
                },
            }
        },
    }
    tools = [
        {
            "name": "search_docs",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        }
    ]

    profile = hub._mcp_query_profile(config)
    chosen = hub._choose_profiled_mcp_tool(tools, profile)
    arguments = hub._build_mcp_tool_arguments(
        profile,
        chosen,
        "chapter context",
        config,
    )

    assert chosen["name"] == "search_docs"
    assert arguments == {"query": "chapter context", "scope": "novel", "limit": 5}


def test_mcp_source_config_accepts_per_server_query_profiles(make_client) -> None:
    client, _ = make_client("mcp_profile_config.db")

    configured = client.put(
        "/integrations/sources/mcp",
        json={
            "config": {
                "server": "story-mcp",
                "workspace": "novel",
                "profiles": {
                    "story-mcp": {
                        "tool": "search_docs",
                        "argument_map": {
                            "query": "{query}",
                            "workspace": "{workspace}",
                        },
                    }
                },
            }
        },
    )

    assert configured.status_code == 200
    config = configured.json()["config"]
    assert config["profiles"]["story-mcp"]["tool"] == "search_docs"
    assert config["profiles"]["story-mcp"]["argument_map"]["workspace"] == "{workspace}"

    rejected = client.put(
        "/integrations/sources/mcp",
        json={
            "config": {
                "server": "story-mcp",
                "profiles": {"story-mcp": {"arguments": {"query": "{query}"}}},
            }
        },
    )
    assert rejected.status_code == 400
    assert "mcp.profiles.story-mcp.tool" in rejected.json()["detail"]


def test_notebooklm_query_uses_configured_mcp_adapter(make_client, monkeypatch) -> None:
    client, _ = make_client("notebooklm_mcp_query.db")
    client.put(
        "/integrations/sources/notebooklm",
        json={
            "config": {
                "notebook_id": "notebook-1",
                "project": "novel",
                "mcp_server": "notebook-mcp",
                "query_profile": {
                    "tool": "search_notebook",
                    "arguments": {
                        "query": "{query}",
                        "notebook": "{notebook_id}",
                    },
                },
            }
        },
    )

    def fake_query_mcp(query: str, config: dict) -> str:
        assert query == "chapter memory"
        assert config["server"] == "notebook-mcp"
        assert config["workspace"] == "novel"
        assert config["notebook_id"] == "notebook-1"
        assert config["query_profile"]["tool"] == "search_notebook"
        return "Notebook references..."

    monkeypatch.setattr(client.app.state.integrations, "_query_mcp", fake_query_mcp)

    queried = client.post(
        "/integrations/query",
        json={"source": "notebooklm", "query": "chapter memory"},
    ).json()

    assert 'NotebookLM MCP query for notebook "notebook-1"' in queried["result"]
    assert "Notebook references..." in queried["result"]


def test_notebooklm_validate_checks_configured_mcp_adapter(make_client, monkeypatch) -> None:
    client, _ = make_client("notebooklm_mcp_validate.db")
    client.put(
        "/integrations/sources/notebooklm",
        json={
            "config": {
                "notebook_id": "notebook-1",
                "mcp_server": "notebook-mcp",
            }
        },
    )
    monkeypatch.setattr(
        client.app.state.integrations,
        "_load_mcp_servers",
        lambda: (
            [
                {
                    "name": "notebook-mcp",
                    "enabled": True,
                    "auth_status": "unsupported",
                    "transport": {"type": "stdio", "command": "notebook-mcp.exe", "args": []},
                }
            ],
            None,
        ),
    )

    validation = client.get("/integrations/sources/notebooklm/validate").json()["validation"]

    assert validation["ok"] is True
    assert any(check["name"] == "mcp_adapter_configured" for check in validation["checks"])
    assert validation["details"]["mcp_server"] == "notebook-mcp"
    assert validation["details"]["mcp_validation"]["source"] == "mcp"


def test_integration_source_config_requires_per_source_fields(make_client) -> None:
    client, _ = make_client("source_validation.db")

    repo_bad = client.put("/integrations/sources/repo", json={"config": {}})
    assert repo_bad.status_code == 400
    assert "repo.root" in repo_bad.json()["detail"]

    notion_bad = client.put(
        "/integrations/sources/notion",
        json={"config": {"workspace_id": "workspace-1"}},
    )
    assert notion_bad.status_code == 400
    assert "notion.token_env" in notion_bad.json()["detail"]

    mcp_good = client.put(
        "/integrations/sources/mcp",
        json={"config": {"server": "local-mcp"}},
    )
    assert mcp_good.status_code == 200
    assert mcp_good.json()["config"]["server"] == "local-mcp"


def test_integration_source_validate_endpoint_reports_readiness(make_client, monkeypatch) -> None:
    client, settings = make_client("source_validate_endpoint.db")

    not_configured = client.get("/integrations/sources/repo/validate")
    assert not_configured.status_code == 200
    first_body = not_configured.json()
    assert first_body["configured"] is False
    assert first_body["validation"]["ok"] is False
    assert any("repo.root" in item for item in first_body["validation"]["errors"])

    repo_root = settings.workspace_root / "manuscript"
    repo_root.mkdir(parents=True, exist_ok=True)
    client.put("/integrations/sources/repo", json={"config": {"root": "manuscript"}})
    repo_ready = client.get("/integrations/sources/repo/validate").json()
    assert repo_ready["validation"]["ok"] is True

    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    client.put(
        "/integrations/sources/notion",
        json={"config": {"workspace_id": "workspace-1", "token_env": "NOTION_TOKEN"}},
    )
    notion_missing_token = client.get("/integrations/sources/notion/validate").json()
    assert notion_missing_token["validation"]["ok"] is False
    assert any(
        check["name"] == "token_env_present" and check["status"] == "fail"
        for check in notion_missing_token["validation"]["checks"]
    )

    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    notion_ready = client.get("/integrations/sources/notion/validate").json()
    assert notion_ready["validation"]["ok"] is True

    unknown = client.get("/integrations/sources/unknown/validate")
    assert unknown.status_code == 400


def test_integration_config_upload_is_strict_and_atomic(make_client) -> None:
    client, _ = make_client("source_upload_strict.db")

    bad_payload = {
        "sources": {
            "repo": {},
            "unknown": {"value": 1},
        }
    }
    rejected = client.post(
        "/integrations/sources/upload",
        json={"filename": "bad.json", "content": json.dumps(bad_payload)},
    )
    assert rejected.status_code == 400
    detail = rejected.json()["detail"]
    assert "repo.root" in detail
    assert "unsupported source" in detail

    listed = client.get("/integrations/sources").json()["sources"]
    assert listed["repo"] == {}


def test_integration_guardrails_allow_reads_block_writes(make_client) -> None:
    client, _ = make_client("integrations.db")
    read_resp = client.post("/integrations/query", json={"source": "mcp", "query": "chapter memory"}).json()
    assert "result" in read_resp

    write_resp = client.post(
        "/integrations/write",
        json={"source": "notion", "operation": "update_page", "payload": {"id": "123"}},
    )
    assert write_resp.status_code == 403
    assert "read/query only" in write_resp.json()["detail"].lower()


def test_preview_reflects_approved_text_in_beat_order(make_client) -> None:
    client, _ = make_client("preview.db")
    chapter_id = create_chapter_with_beats(client, beat_count=3)
    run = client.post("/runs", json={"chapter_id": chapter_id, "mode": "fast"}).json()
    run_id = int(run["id"])
    wait_for_status(client, run_id, {"completed"})

    preview = client.get(f"/chapters/{chapter_id}/preview").json()["preview"]
    pos_1 = preview.find("Beat 1: pressure escalates 1")
    pos_2 = preview.find("Beat 2: pressure escalates 2")
    pos_3 = preview.find("Beat 3: pressure escalates 3")
    assert pos_1 != -1 and pos_2 != -1 and pos_3 != -1
    assert pos_1 < pos_2 < pos_3

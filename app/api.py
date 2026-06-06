from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import difflib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.db import Database
from app.integrations import ReadOnlyIntegrationHub, WRITE_BLOCK_MESSAGE
from app.orchestrator import RunOrchestrator
from app.providers.factory import build_provider
from app.repository import Repository
from app.schemas import (
    BeatsReplaceRequest,
    ChapterPublishRequest,
    ChapterCreateRequest,
    ChapterUpdateRequest,
    ChatRequest,
    ChatResponse,
    IntegrationConfigUploadRequest,
    MCPSourceConfig,
    NotebookLMSourceConfig,
    NotionSourceConfig,
    IntegrationQueryRequest,
    RepoSourceConfig,
    IntegrationSourceConfigRequest,
    IntegrationWriteRequest,
    ManuscriptLinkRequest,
    ManuscriptUploadRequest,
    RunActionRequest,
    RunCreateRequest,
)


KNOWN_SOURCES = {"repo", "notion", "mcp", "notebooklm"}
SOURCE_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "repo": RepoSourceConfig,
    "notion": NotionSourceConfig,
    "mcp": MCPSourceConfig,
    "notebooklm": NotebookLMSourceConfig,
}


def _build_state(settings: Settings) -> tuple[Database, Repository, RunOrchestrator, ReadOnlyIntegrationHub]:
    db = Database(settings.db_path)
    db.init()
    repo = Repository(db)
    provider = build_provider(settings)
    orchestrator = RunOrchestrator(repo, provider)
    integrations = ReadOnlyIntegrationHub(settings.workspace_root)
    return db, repo, orchestrator, integrations


def _resolve_path_inside_workspace(workspace_root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path.strip())
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    candidate = candidate.resolve()
    workspace = workspace_root.resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path must stay inside workspace root.") from exc
    return candidate


def _workspace_relative(workspace_root: Path, target: Path) -> str:
    return str(target.resolve().relative_to(workspace_root.resolve())).replace("\\", "/")


def _build_publish_diff(canonical_path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{canonical_path} (current)",
            tofile=f"{canonical_path} (preview)",
        )
    )


def _publish_backup_path(target: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = target.with_name(f"{target.name}.{timestamp}.bak")
    counter = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}.{timestamp}.{counter}.bak")
        counter += 1
    return candidate


def _safe_filename(name: str) -> str:
    cleaned = Path(name.strip()).name
    cleaned = cleaned.replace("\\", "_").replace("/", "_")
    if not cleaned:
        raise HTTPException(status_code=400, detail="Filename cannot be empty.")
    return cleaned


def _validate_source_config(source: str, config: dict[str, Any]) -> dict[str, Any]:
    model = SOURCE_CONFIG_MODELS[source]
    try:
        validated = model.model_validate(config)
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            path = ".".join(str(part) for part in err.get("loc", []))
            message = err.get("msg", "invalid value")
            if path:
                problems.append(f"{source}.{path}: {message}")
            else:
                problems.append(f"{source}: {message}")
        detail = "Invalid source config: " + "; ".join(problems)
        raise HTTPException(status_code=400, detail=detail) from exc
    return validated.model_dump(exclude_none=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    _, repo, orchestrator, integrations = _build_state(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        orchestrator.recover_runs()
        try:
            yield
        finally:
            orchestrator.shutdown()

    app = FastAPI(title="Novel Agent Cockpit", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.repo = repo
    app.state.orchestrator = orchestrator
    app.state.integrations = integrations
    app.state.provider = orchestrator.provider

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "provider": app.state.provider.name}

    @app.post("/chapters")
    async def create_chapter(payload: ChapterCreateRequest) -> dict:
        return repo.create_chapter(payload.title, payload.canonical_path)

    @app.get("/chapters")
    async def list_chapters() -> list[dict]:
        return repo.list_chapters()

    @app.get("/chapters/{chapter_id}")
    async def get_chapter(chapter_id: int) -> dict:
        chapter = repo.get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found.")
        return chapter

    @app.patch("/chapters/{chapter_id}")
    async def update_chapter(chapter_id: int, payload: ChapterUpdateRequest) -> dict:
        chapter = repo.get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found.")
        title = payload.title.strip() if payload.title else None
        canonical_path = payload.canonical_path.strip() if payload.canonical_path else None
        if title is None and canonical_path is None:
            return chapter
        if canonical_path is not None:
            target = _resolve_path_inside_workspace(settings.workspace_root, canonical_path)
            canonical_path = _workspace_relative(settings.workspace_root, target)
        updated = repo.update_chapter(
            chapter_id,
            title=title if title is not None else chapter["title"],
            canonical_path=canonical_path if canonical_path is not None else chapter["canonical_path"],
        )
        return updated

    @app.get("/manuscript/files")
    async def list_manuscript_files() -> dict:
        manuscript_root = settings.workspace_root / "manuscript"
        files: list[dict[str, Any]] = []
        if manuscript_root.exists():
            for path in sorted(manuscript_root.rglob("*")):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".md", ".markdown", ".txt"}:
                    continue
                rel = _workspace_relative(settings.workspace_root, path)
                linked = bool(repo.get_chapter_by_canonical_path(rel))
                files.append(
                    {
                        "path": rel,
                        "size_bytes": path.stat().st_size,
                        "linked": linked,
                    }
                )
        return {"workspace_root": str(settings.workspace_root), "files": files}

    @app.post("/manuscript/upload")
    async def upload_manuscript(payload: ManuscriptUploadRequest) -> dict:
        safe_name = _safe_filename(payload.filename)
        target = _resolve_path_inside_workspace(settings.workspace_root, f"manuscript/{safe_name}")
        if target.exists() and not payload.overwrite:
            raise HTTPException(status_code=409, detail="File exists. Set overwrite=true to replace it.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload.content, encoding="utf-8")

        canonical_path = _workspace_relative(settings.workspace_root, target)
        existing = repo.get_chapter_by_canonical_path(canonical_path)
        chapter_title = (payload.chapter_title or target.stem).strip()
        if existing:
            chapter = repo.update_chapter(existing["id"], title=chapter_title, canonical_path=canonical_path)
            linked = "existing"
        else:
            chapter = repo.create_chapter(chapter_title, canonical_path)
            linked = "created"
        return {"chapter": chapter, "canonical_path": canonical_path, "linked": linked}

    @app.post("/chapters/link-manuscript")
    async def link_manuscript(payload: ManuscriptLinkRequest) -> dict:
        target = _resolve_path_inside_workspace(settings.workspace_root, payload.canonical_path)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="Manuscript file not found.")
        canonical_path = _workspace_relative(settings.workspace_root, target)
        existing = repo.get_chapter_by_canonical_path(canonical_path)
        chapter_title = (payload.chapter_title or target.stem).strip()
        if existing:
            chapter = repo.update_chapter(existing["id"], title=chapter_title, canonical_path=canonical_path)
            linked = "existing"
        else:
            chapter = repo.create_chapter(chapter_title, canonical_path)
            linked = "created"
        return {"chapter": chapter, "canonical_path": canonical_path, "linked": linked}

    @app.post("/chapters/{chapter_id}/beats")
    async def replace_beats(chapter_id: int, payload: BeatsReplaceRequest) -> list[dict]:
        chapter = repo.get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found.")
        beats = [beat.strip() for beat in payload.beats if beat.strip()]
        if not beats:
            raise HTTPException(status_code=400, detail="At least one beat is required.")
        return repo.replace_beats(chapter_id, beats)

    @app.get("/chapters/{chapter_id}/beats")
    async def list_beats(chapter_id: int) -> list[dict]:
        chapter = repo.get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found.")
        return repo.list_beats(chapter_id)

    @app.post("/chapters/{chapter_id}/lock-spine")
    async def lock_spine(chapter_id: int) -> dict:
        chapter = repo.get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found.")
        beats = repo.lock_all_beats(chapter_id)
        return {"chapter_id": chapter_id, "locked_count": len(beats), "beats": beats}

    @app.get("/chapters/{chapter_id}/preview")
    async def chapter_preview(chapter_id: int) -> dict:
        chapter = repo.get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found.")
        return {"chapter_id": chapter_id, "preview": repo.assemble_preview(chapter_id)}

    @app.post("/chapters/{chapter_id}/publish")
    async def publish_chapter(chapter_id: int, payload: ChapterPublishRequest) -> dict:
        chapter = repo.get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found.")
        preview = repo.assemble_preview(chapter_id)
        if not preview.strip():
            raise HTTPException(status_code=400, detail="No approved preview content available to publish.")

        target = _resolve_path_inside_workspace(settings.workspace_root, chapter["canonical_path"])
        target_existed = target.exists()
        if target_existed and not payload.overwrite and not payload.dry_run:
            raise HTTPException(status_code=409, detail="Target file exists. Set overwrite=true to publish.")
        output = preview if preview.endswith("\n") else f"{preview}\n"
        existing_content = target.read_text(encoding="utf-8") if target_existed else ""
        canonical_path = _workspace_relative(settings.workspace_root, target)
        diff = _build_publish_diff(canonical_path, existing_content, output)
        would_write_bytes = len(output.encode("utf-8"))

        if payload.dry_run:
            return {
                "chapter_id": chapter_id,
                "canonical_path": canonical_path,
                "bytes_written": 0,
                "would_write_bytes": would_write_bytes,
                "status": "preview",
                "target_exists": target_existed,
                "backup_path": None,
                "diff": diff,
            }

        target.parent.mkdir(parents=True, exist_ok=True)
        backup_path = None
        if target_existed and payload.backup:
            backup_target = _publish_backup_path(target)
            backup_target.write_text(existing_content, encoding="utf-8")
            backup_path = _workspace_relative(settings.workspace_root, backup_target)
        target.write_text(output, encoding="utf-8")
        repo.set_chapter_status(chapter_id, "published")
        return {
            "chapter_id": chapter_id,
            "canonical_path": canonical_path,
            "bytes_written": would_write_bytes,
            "status": "published",
            "target_exists": target_existed,
            "backup_path": backup_path,
            "diff": diff,
        }

    @app.post("/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest) -> ChatResponse:
        chapter_title = None
        if payload.chapter_id:
            chapter = repo.get_chapter(payload.chapter_id)
            if chapter:
                chapter_title = chapter["title"]
        reply = await app.state.provider.brainstorm(payload.message, chapter_title=chapter_title)
        return ChatResponse(reply=reply, provider=app.state.provider.name)

    @app.post("/runs")
    async def create_run(payload: RunCreateRequest) -> dict:
        chapter = repo.get_chapter(payload.chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found.")
        run = repo.create_run(payload.chapter_id, payload.mode)
        repo.append_event(
            run["id"],
            "run_created",
            {"mode": payload.mode, "chapter_id": payload.chapter_id},
        )
        try:
            orchestrator.prepare_run(run["id"])
            orchestrator.dispatch_run(run["id"])
        except Exception as exc:
            repo.update_run(run["id"], status="failed", last_error=str(exc), ended=True)
            repo.append_event(run["id"], "run_failed", {"error": str(exc)})
        return repo.get_run(run["id"])

    @app.get("/runs/{run_id}")
    async def get_run(run_id: int) -> dict:
        run = repo.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found.")
        return run

    @app.get("/runs/{run_id}/todos")
    async def list_run_todos(run_id: int) -> list[dict]:
        run = repo.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found.")
        return repo.list_todos(run_id)

    @app.post("/runs/{run_id}/actions")
    async def run_action(run_id: int, payload: RunActionRequest) -> dict:
        run = repo.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found.")
        try:
            result = await orchestrator.handle_action(
                run_id=run_id,
                action=payload.action,
                beat_id=payload.beat_id,
                notes=payload.notes,
            )
            if isinstance(result, dict) and result.get("id") == run_id:
                return result
            return {"ok": True, "result": result, "run": repo.get_run(run_id)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/runs/{run_id}/events/history")
    async def run_event_history(run_id: int, after_id: int = 0) -> list[dict]:
        run = repo.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found.")
        return repo.list_events(run_id, after_id=after_id)

    @app.get("/runs/{run_id}/events")
    async def run_events_stream(run_id: int, request: Request, after_id: int = 0) -> StreamingResponse:
        run = repo.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found.")

        async def event_generator() -> AsyncIterator[str]:
            cursor = after_id
            while True:
                if await request.is_disconnected():
                    break
                events = repo.list_events(run_id, after_id=cursor)
                for event in events:
                    cursor = event["id"]
                    payload = event.get("payload", {})
                    yield f"id: {event['id']}\n"
                    yield f"event: {event['event_type']}\n"
                    yield f"data: {json.dumps(payload)}\n\n"
                current = repo.get_run(run_id)
                if current and current["status"] in {"completed", "failed", "canceled"} and not events:
                    break
                await asyncio.sleep(0.5)

        headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
        return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)

    @app.post("/integrations/query")
    async def integrations_query(payload: IntegrationQueryRequest) -> dict:
        config_row = repo.get_integration_source_config(payload.source)
        config = config_row.get("config", {})
        result = integrations.query(payload.source, payload.query, config=config)
        return {"source": payload.source, "query": payload.query, "result": result, "config": config}

    @app.get("/integrations/sources")
    async def list_integration_sources() -> dict:
        rows = repo.list_integration_source_configs()
        mapped = {item["source"]: item.get("config", {}) for item in rows}
        for source in KNOWN_SOURCES:
            mapped.setdefault(source, {})
        return {"sources": mapped}

    @app.get("/integrations/sources/{source}/validate")
    async def validate_integration_source(source: str) -> dict:
        normalized = source.lower().strip()
        if normalized not in KNOWN_SOURCES:
            raise HTTPException(status_code=400, detail=f"Unsupported source '{source}'.")
        row = repo.get_integration_source_config(normalized)
        config = row.get("config", {})
        validation = integrations.validate_source(normalized, config=config)
        return {
            "source": normalized,
            "configured": bool(config),
            "config": config,
            "validation": validation,
        }

    @app.put("/integrations/sources/{source}")
    async def upsert_integration_source(source: str, payload: IntegrationSourceConfigRequest) -> dict:
        normalized = source.lower().strip()
        if normalized not in KNOWN_SOURCES:
            raise HTTPException(status_code=400, detail=f"Unsupported source '{source}'.")
        validated = _validate_source_config(normalized, payload.config)
        row = repo.upsert_integration_source_config(normalized, validated)
        return {"source": normalized, "config": row.get("config", {})}

    @app.post("/integrations/sources/upload")
    async def upload_integration_sources(payload: IntegrationConfigUploadRequest) -> dict:
        try:
            parsed = json.loads(payload.content)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON config upload: {exc.msg}") from exc

        raw_sources: dict[str, Any]
        if isinstance(parsed, dict) and isinstance(parsed.get("sources"), dict):
            raw_sources = parsed["sources"]
        elif isinstance(parsed, dict):
            raw_sources = parsed
        else:
            raise HTTPException(status_code=400, detail="Integration config must be a JSON object.")

        validated_sources: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for source, config in raw_sources.items():
            normalized = str(source).lower().strip()
            if normalized not in KNOWN_SOURCES:
                errors.append(f"{source}: unsupported source")
                continue
            if not isinstance(config, dict):
                errors.append(f"{source}: config must be an object")
                continue
            try:
                validated = _validate_source_config(normalized, config)
            except HTTPException as exc:
                errors.append(str(exc.detail))
                continue
            validated_sources[normalized] = validated

        if errors:
            raise HTTPException(
                status_code=400,
                detail="Config upload failed validation: " + "; ".join(errors),
            )
        if not validated_sources:
            raise HTTPException(status_code=400, detail="No supported source entries found in uploaded config.")

        updated: dict[str, dict[str, Any]] = {}
        for source, config in validated_sources.items():
            row = repo.upsert_integration_source_config(source, config)
            updated[source] = row.get("config", {})
        return {"filename": payload.filename, "updated_sources": updated}

    @app.post("/integrations/write")
    async def integrations_write(payload: IntegrationWriteRequest) -> JSONResponse:
        try:
            integrations.write(payload.source, payload.operation, payload.payload)
        except PermissionError:
            raise HTTPException(status_code=403, detail=WRITE_BLOCK_MESSAGE)
        return JSONResponse({"ok": True})

    return app


app = create_app()

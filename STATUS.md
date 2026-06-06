# Novel Agent Cockpit - Project Status

Last updated: 2026-06-06

This is the tracked continuation document for the project. Read this file before
starting a new implementation session, and update it after each validated backend
milestone.

## Current State

The project is a runnable local FastAPI application with SQLite persistence. The
core workflow exists:

- Chat and agent modes
- Spine creation, review, and locking
- Fast and section-by-section execution
- Todo-driven run orchestration
- Pause, resume, cancellation, and restart recovery
- Approved-fragment preview
- SSE run event stream and replay history
- Manuscript upload, linking, dry-run publishing, backups, and overwrite publishing
- Runtime configuration for repo, Notion, MCP, and NotebookLM sources
- Read-only integration querying with integration writes intentionally blocked

The repository is initialized and connected to:

- `origin`: `https://github.com/Lostenergydrink/novel-agent-cockpit.git`
- Default branch currently in use: `master`

## Verification

Latest backend verification:

- Command: `py -m pytest`
- Result: **24 passed, 0 failed**
- Verified: 2026-06-06

The tests cover the main run loop, section approval and revision, pause/resume
recovery, the pause-generation race, manuscript publishing safeguards, source
configuration validation, and mocked integration adapter behavior.

## Important Limitation

The application currently defaults to the deterministic mock writing provider
unless an OpenAI key and provider configuration are supplied. The OpenAI provider
has not yet received a full real-world reliability and quality pass.

Configured integrations are query tools beside the writing engine. Their results,
the linked manuscript, prior approved prose, and other story references are not yet
assembled into the model context used for drafting.

## Critical Known Defect

Fast mode currently creates a `draft_scene` todo and a `polish_transition` todo for
the same beat. Both outputs are stored as approved fragments for that beat, while
preview assembly selects only the latest approved fragment per beat.

Result: for every non-final beat, the transition can replace the scene draft in the
assembled preview even though the run reports successful completion.

This is the next backend issue to fix.

## Next Milestone: Draft Correctness

1. Associate generated fragments with the todo or task that produced them.
2. Assemble preview content in todo order so scenes and transitions are preserved.
3. Ensure revision approval replaces only the intended scene fragment.
4. Add a SQLite migration path for the fragment relationship.
5. Add regression coverage proving that a three-beat fast run produces three scenes
   and two transitions in the correct order.
6. Re-run the full test suite and perform an isolated manuscript smoke test.

## After Draft Correctness

Recommended order:

1. Prevent canceled runs from accepting or publishing late model output.
2. Define multiple-run and approved-version behavior for the same chapter.
3. Protect spine replacement when active runs or approved drafts depend on it.
4. Build a generation context package from manuscript text, approved prose, spine,
   and selected integration results.
5. Add provider timeouts, retries, cancellation handling, and visible error details.
6. Run live OpenAI, Notion, and selected MCP smoke tests.
7. Add database migration tooling, backup/restore, and stronger operational logging.

## Deferred Work

These items are useful but should not displace the correctness and context work:

- Live MCP profile validation against the server's actual tool list
- Non-stdio semantic MCP querying
- Stricter Notion workspace/database filtering
- Native NotebookLM API support
- Git branch/commit publishing mode
- Authentication and multi-user permissions
- External production queue and deployment hardening
- Frontend migration and visual polish

## Environment Notes

- Manuscript discovery is rooted at `WORKSPACE_ROOT/manuscript`.
- Runtime data and local databases under `data/` are ignored by Git.
- Integration config is persisted in SQLite and should not contain secret values.
- Notion tokens are loaded indirectly through the configured environment variable.
- Local Codex MCP inspection uses `model_reasoning_effort=high` to avoid incompatible
  global configuration values.
- Windows subprocess execution includes a `cmd.exe` fallback for `codex.CMD`
  permission-resolution failures.

## Useful Paths

- Application entry point: `app/main.py`
- API: `app/api.py`
- Run orchestration: `app/orchestrator.py`
- Persistence: `app/repository.py`
- Database initialization: `app/db.py`
- Integrations: `app/integrations.py`
- Providers: `app/providers/`
- Tests: `tests/test_workflow.py`

# Novel Agent Cockpit (MVP)

Local-first writing cockpit that emulates a GLM-style Chat/Agent workflow:

Current backend status, known gaps, and the next recommended milestone are tracked in
[`STATUS.md`](STATUS.md).

- Chat <-> Agent toggle in one workspace
- Spine build/review/lock flow
- Manuscript upload/link flow (choose what exists, not hardcoded)
- Mode execution: `fast` or `section`
- Todo-driven run engine with checkpoints
- Live preview of approved chapter draft
- Publish approved preview to canonical manuscript path with dry-run diff and overwrite backup
- Read/query integration adapters with write-block guardrails
- Runtime source configuration upload (repo/notion/mcp/notebooklm)

## Quick Start

1. Install dependencies:

```bash
pip install -e ".[dev]"
```

2. Optional OpenAI setup (falls back to deterministic mock provider if missing):

```bash
set OPENAI_API_KEY=your_key_here
set OPENAI_MODEL=gpt-4.1-mini
set MODEL_PROVIDER=openai
```

3. Run the app:

```bash
uvicorn app.main:app --reload
```

4. Open:

`http://127.0.0.1:8000`

## API Highlights

- `POST /runs` create a run (`mode=fast|section`, `chapter_id`)
- `POST /runs/{id}/actions` (`revise`, `lock_beat`, `approve`, `pause`, `resume`, `cancel`)
- `GET /runs/{id}` run status + checkpoint
- `GET /runs/{id}/events` SSE stream
- `GET /chapters/{id}/preview` assembled approved draft
- `POST /chapters/{id}/publish` preview or write approved preview to canonical path
- `POST /manuscript/upload` upload manuscript file and auto-link/create chapter
- `GET /manuscript/files` list discovered manuscript files
- `POST /chapters/link-manuscript` link an existing manuscript file to a chapter record
- `POST /integrations/sources/upload` upload JSON source config
- `GET /integrations/sources` view current runtime source config
- `GET /integrations/sources/{source}/validate` run source-specific readiness checks
- `POST /integrations/query` execute source queries (repo filename search, live Notion search, MCP server introspection)

Publish accepts `overwrite`, `dry_run`, and `backup`. A dry run returns a unified diff without writing. A real overwrite creates a timestamped backup by default.

### Source Config Required Fields

- `repo`: `root`
- `notion`: `workspace_id`, `token_env`
- `mcp`: `server`
- `notebooklm`: `notebook_id`

Upload is strict and atomic: if any entry is invalid, the whole upload is rejected.

Validation checks are local and source-specific. For example:
- `repo`: root path exists and stays inside workspace
- `notion`: configured token env var is present
- `mcp`: local Codex server registration and transport readiness
- `notebooklm`: config shape validated, with optional MCP adapter readiness when `mcp_server` is configured

### Integration Query Notes

- `repo` queries search filenames under configured `repo.root`.
- `notion` queries call Notion Search API using `notion.token_env` at runtime.
- `mcp` queries inspect local Codex MCP server configuration (`codex mcp list/get --json`) and return live server metadata for the configured `mcp.server`.
- For MCP servers using `stdio` transport, queries now attempt semantic tool execution: discover tools, choose a query-like tool, and run `tools/call` with your query text.
- Optional `mcp.toolset` can be a comma-separated preferred tool name list (for example: `search_docs,query`).
- Optional `mcp.query_profile` or `mcp.profiles.<server>` can name the exact tool and argument templates:

```json
{
  "mcp": {
    "server": "story-mcp",
    "workspace": "novel",
    "profiles": {
      "story-mcp": {
        "tool": "search_docs",
        "arguments": {
          "query": "{query}",
          "scope": "{workspace}"
        }
      }
    }
  }
}
```

- Non-stdio MCP transports currently return metadata fallback while preserving query intent.
- `notebooklm` queries can run live through a configured MCP adapter:

```json
{
  "notebooklm": {
    "notebook_id": "story-notebook",
    "mcp_server": "notebook-mcp",
    "query_profile": {
      "tool": "search_notebook",
      "arguments": {
        "query": "{query}",
        "notebook": "{notebook_id}"
      }
    }
  }
}
```

- Integration writes remain blocked in Phase 1 (`/integrations/write` returns 403).

## Tests

```bash
pytest
```

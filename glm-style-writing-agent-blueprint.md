# GLM-Style Writing Agent Blueprint (Novel Workflow)

Date: 2026-05-27

## Goal
Recreate the GLM chat/agent writing experience while removing the constraints that block your workflow:
- no 10-file upload ceiling
- full access to local repo + Notion + MCP tools
- durable progress (no context loss when a sandbox sleeps)
- visible live chapter preview while generation is running

## What We Need To Emulate
1. Chat mode and Agent mode toggle in one interface.
2. Writing submodes:
   - Fast writing (full chapter run)
   - Section-by-section (beat/scene granular)
3. Collaborative spine locking flow:
   - propose beats
   - revise beats
   - lock beats
4. A todo-driven execution engine that turns locked beats into chapter output.
5. A side preview panel that updates as beats/scenes are drafted.
6. Long-running jobs (15-30 minutes) with pause/resume and durable state.

## Architecture Options

### Option A: Fastest Path (Codex + local files + scripted loop)
Use a light workflow wrapper around Codex + your repo.

Pros
- Zero app build to start
- Immediate use of local files and MCP
- Cheapest and fastest to validate process

Cons
- No polished toggle UI
- No true live side-panel preview without extra UI work
- More manual orchestration between steps

Best for
- Proving your beat-lock + todo flow this week

### Option B: Recommended (Custom local Writing Cockpit)
Build a small web app with:
- Left: chat panel
- Center: agent run status/todo panel
- Right: live chapter preview

Suggested stack
- Frontend: Next.js (or similar) single-page app
- Orchestrator: OpenAI Agents SDK (Python or JS)
- Persistence: SQLite first, PostgreSQL later
- Tooling: MCP servers (your custom server + NotebookLM MCP + others)
- Integrations: Notion API + GitHub API/MCP

Why this is the fit
- Recreates GLM UX behavior closely
- Keeps your repo/Notion/MCP as first-class citizens
- Gives durable state + resumable runs + audit logs

### Option C: Heavy-Duty Workflow Engine (LangGraph/Temporal style)
Same UX as Option B, but orchestrator is a durable workflow framework.

Pros
- Excellent pause/resume reliability for long or interrupted runs
- Strong control of human-in-the-loop steps

Cons
- Extra complexity, more setup time
- Overkill unless you need high concurrency or multi-user scale soon

Best for
- Team usage, production uptime, large multi-run queues

## Recommended Build Path

### Phase 1 (2-4 days): MVP You Can Actually Use
1. Build one chapter workspace screen:
   - Chat/Agent toggle
   - Mode selector (Fast / Section)
   - Preview pane
2. Implement chapter state model in SQLite:
   - chapter
   - beats[]
   - lock_state
   - todo_queue
   - generated_sections[]
   - run_log
3. Implement section mode loop:
   - draft beat
   - user revise/approve
   - lock beat
   - move to next beat
4. Write live preview after every accepted beat.

Output of Phase 1
- Reliable section-by-section drafting with live preview and resumable progress.

### Phase 2 (3-5 days): Fast Writing Mode + Background Runs
1. Add ?Fast writing? execution path:
   - consume locked spine
   - generate full chapter in one run
2. Add run monitor:
   - status: queued / in_progress / waiting_approval / completed / failed
3. Add cancellation + resume controls.
4. Add run replay logs for trust/debug.

Output of Phase 2
- One-shot chapters with visibility, checkpoints, and recovery.

### Phase 3 (2-4 days): Integrations and Guardrails
1. Notion sync:
   - beat board/status in Notion
   - chapter metadata and revision history links
2. GitHub integration:
   - write approved chapter files to repo
   - optional branch/commit flow per chapter
3. Tool approvals:
   - require approval for write/delete actions
   - auto-approve read/query actions

Output of Phase 3
- Fully connected writing cockpit that is safer and more auditable than GLM.

## Data Model (Minimum)
- Project
- Chapter
- Beat
  - id
  - summary
  - status (draft/revise/locked)
  - notes
- TodoItem
  - beat_id
  - task_type (draft_scene/revise_line/polish_transition)
  - status
- DraftFragment
  - chapter_id
  - beat_id
  - text
  - version
- Run
  - mode (fast/section)
  - status
  - started_at
  - ended_at
  - checkpoint_blob

## UX Pattern To Copy Exactly
- Button: Chat <-> Agent
- Agent mode starts with:
  - "Build spine"
  - "Review spine"
  - "Lock spine"
  - "Execute mode"
- During execution:
  - running checklist
  - currently active beat
  - live chapter preview
- End of run:
  - chapter assembled
  - unresolved todo surfaced
  - resume button if interrupted

## Practical Implementation Notes
- Treat your novel repo as source of truth for chapter text.
- Treat Notion as planning/visibility layer, not canonical chapter storage.
- Persist run state every beat so interruption never loses progress.
- Keep your voice profile as a compact style artifact (not giant static prompt).

## Key Technical Guardrails
- Never rely on a single "session summary file" as sole memory.
- Persist structured state every step (beats, todo, preview text, run checkpoints).
- Separate generated draft text from approved/canon text.
- Add explicit lock/unlock transitions for beats and chapters.

## What This Solves vs GLM
- No upload cap: full local repo and connectors
- No sandbox amnesia: durable local run state
- Better transparency: explicit run logs + task checkpoints
- Better control of voice: granular beat/line workflow and approvals

## First Build Decision To Make
Choose one:
1. Build Phase 1 directly as local web app (recommended)
2. Prototype process loop in Codex first, then build UI
3. Go straight to durable workflow framework (LangGraph/Temporal style)


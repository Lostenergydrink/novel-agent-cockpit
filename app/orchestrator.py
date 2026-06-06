from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from datetime import datetime, timezone
from typing import Any

from app.models import PAUSABLE_RUN_STATUSES, TERMINAL_RUN_STATUSES
from app.providers.base import WritingProvider
from app.repository import Repository


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunOrchestrator:
    def __init__(self, repo: Repository, provider: WritingProvider) -> None:
        self.repo = repo
        self.provider = provider
        self._runner_thread: threading.Thread | None = None
        self._runner_loop: asyncio.AbstractEventLoop | None = None
        self._runner_ready = threading.Event()
        self._runner_lock = threading.RLock()
        self._scheduled: dict[int, concurrent.futures.Future] = {}

    async def start_run(self, run_id: int) -> dict[str, Any]:
        run = self.repo.get_run(run_id)
        if not run:
            raise ValueError(f"Run {run_id} does not exist")
        if run["status"] in TERMINAL_RUN_STATUSES:
            return run

        self._ensure_todos(run)
        run = self.repo.get_run(run_id)

        if run["mode"] == "fast":
            await self._execute_fast_mode(run_id)
        elif run["mode"] == "section":
            await self._execute_section_mode(run_id)
        else:
            raise ValueError(f"Unsupported mode: {run['mode']}")
        return self.repo.get_run(run_id)

    def prepare_run(self, run_id: int) -> dict[str, Any]:
        run = self.repo.get_run(run_id)
        if not run:
            raise ValueError(f"Run {run_id} does not exist")
        self._ensure_todos(run)
        return self.repo.get_run(run_id)

    def dispatch_run(self, run_id: int) -> dict[str, Any]:
        run = self.repo.get_run(run_id)
        if not run:
            raise ValueError(f"Run {run_id} does not exist")
        if run["status"] in TERMINAL_RUN_STATUSES:
            return run
        loop = self._ensure_runner_loop()

        with self._runner_lock:
            existing = self._scheduled.get(run_id)
            if existing and not existing.done():
                return self.repo.get_run(run_id)

            future = asyncio.run_coroutine_threadsafe(self.start_run(run_id), loop)
            self._scheduled[run_id] = future

            def _cleanup(done_future: concurrent.futures.Future) -> None:
                with self._runner_lock:
                    self._scheduled.pop(run_id, None)
                try:
                    _ = done_future.result()
                except Exception as exc:
                    self.repo.update_run(run_id, status="failed", last_error=str(exc), ended=True)
                    self.repo.append_event(run_id, "run_failed", {"error": str(exc), "at": utc_now()})

            future.add_done_callback(_cleanup)
        return self.repo.get_run(run_id)

    def recover_runs(self) -> None:
        # If the app restarts during execution, we cannot trust in-progress todo state.
        # We move those items back to pending and park runs as paused for explicit resume.
        in_progress_runs = self.repo.list_runs_by_statuses(["in_progress"])
        for run in in_progress_runs:
            self.repo.reset_in_progress_todos_to_pending(run["id"])
            self.repo.update_run(
                run["id"],
                status="paused",
                active_todo_id=None,
            )
            self.repo.append_event(
                run["id"],
                "run_recovered_paused",
                {"at": utc_now(), "reason": "app_restart_recovery"},
            )

        queued_runs = self.repo.list_runs_by_statuses(["queued"])
        for run in queued_runs:
            self.dispatch_run(run["id"])

    def shutdown(self) -> None:
        with self._runner_lock:
            for _, future in list(self._scheduled.items()):
                future.cancel()
            self._scheduled.clear()

            loop = self._runner_loop
            thread = self._runner_thread
            self._runner_loop = None
            self._runner_thread = None
            self._runner_ready.clear()

        if loop:
            loop.call_soon_threadsafe(loop.stop)
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def _ensure_runner_loop(self) -> asyncio.AbstractEventLoop:
        with self._runner_lock:
            if self._runner_loop and self._runner_thread and self._runner_thread.is_alive():
                return self._runner_loop

            self._runner_ready.clear()

            def _runner() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                with self._runner_lock:
                    self._runner_loop = loop
                self._runner_ready.set()
                loop.run_forever()
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

            thread = threading.Thread(target=_runner, name="novel-agent-runner", daemon=True)
            thread.start()
            self._runner_thread = thread

        self._runner_ready.wait(timeout=2.0)
        if not self._runner_loop:
            raise RuntimeError("Failed to initialize run dispatcher loop.")
        return self._runner_loop

    def _ensure_todos(self, run: dict[str, Any]) -> None:
        if self.repo.count_todos(run["id"]) > 0:
            return
        locked_beats = self.repo.list_beats(run["chapter_id"], status="locked")
        if not locked_beats:
            raise ValueError("Run requires locked beats before execution.")
        self.repo.seed_todos_for_run(run["id"], [beat["id"] for beat in locked_beats])
        self.repo.append_event(
            run["id"],
            "todo_seeded",
            {
                "todo_count": self.repo.count_todos(run["id"]),
                "generated_at": utc_now(),
            },
        )

    async def _execute_fast_mode(self, run_id: int) -> None:
        try:
            run = self.repo.transition_run_status(
                run_id,
                from_statuses=["queued", "in_progress"],
                to_status="in_progress",
            )
            if not run:
                return
            self.repo.append_event(run_id, "run_started", {"mode": "fast", "at": utc_now()})
            while True:
                run = self.repo.get_run(run_id)
                if not run or run["status"] in TERMINAL_RUN_STATUSES:
                    return
                if run["status"] == "paused":
                    self.repo.append_event(run_id, "run_paused", {"at": utc_now()})
                    return

                todo = self.repo.next_pending_todo(run_id)
                if not todo:
                    self.repo.update_run(run_id, status="completed", ended=True)
                    self.repo.append_event(run_id, "run_completed", {"at": utc_now()})
                    return

                await self._execute_todo(run, todo, auto_approve=True)
                self._update_checkpoint(run_id)
                await asyncio.sleep(0.01)
        except Exception as exc:
            self.repo.update_run(run_id, status="failed", last_error=str(exc), ended=True)
            self.repo.append_event(run_id, "run_failed", {"error": str(exc), "at": utc_now()})

    async def _execute_section_mode(self, run_id: int) -> None:
        try:
            run = self.repo.get_run(run_id)
            if run["status"] == "paused":
                return
            transitioned = self.repo.transition_run_status(
                run_id,
                from_statuses=["queued", "in_progress"],
                to_status="in_progress",
            )
            if not transitioned:
                return
            todo = self.repo.next_pending_todo(run_id)
            if not todo:
                self.repo.update_run(run_id, status="completed", ended=True)
                self.repo.append_event(run_id, "run_completed", {"at": utc_now()})
                return
            await self._execute_todo(run, todo, auto_approve=False)
            self._update_checkpoint(run_id)
        except Exception as exc:
            self.repo.update_run(run_id, status="failed", last_error=str(exc), ended=True)
            self.repo.append_event(run_id, "run_failed", {"error": str(exc), "at": utc_now()})

    async def _execute_todo(self, run: dict[str, Any], todo: dict[str, Any], auto_approve: bool) -> None:
        todo = self.repo.transition_todo_status(
            todo["id"],
            from_statuses=["pending"],
            to_status="in_progress",
        )
        if not todo:
            return
        transitioned = self.repo.transition_run_status(
            run["id"],
            from_statuses=["in_progress"],
            to_status="in_progress",
            active_todo_id=todo["id"],
        )
        if not transitioned:
            self.repo.transition_todo_status(
                todo["id"],
                from_statuses=["in_progress"],
                to_status="pending",
            )
            return
        beat = self.repo.get_beat(todo["beat_id"])
        chapter = self.repo.get_chapter(run["chapter_id"])
        text = await self._generate_text(todo["task_type"], beat["summary"], chapter["title"], run, beat)

        fragment = self.repo.create_fragment(
            chapter_id=chapter["id"],
            beat_id=beat["id"],
            run_id=run["id"],
            text=text,
            approved_flag=1 if auto_approve else 0,
        )

        if auto_approve:
            done_todo = self.repo.transition_todo_status(
                todo["id"],
                from_statuses=["in_progress"],
                to_status="done",
            )
            if not done_todo:
                return
            self.repo.append_event(
                run["id"],
                "fragment_approved",
                {"todo_id": todo["id"], "beat_id": beat["id"], "fragment_id": fragment["id"]},
            )
        else:
            waiting_todo = self.repo.transition_todo_status(
                todo["id"],
                from_statuses=["in_progress"],
                to_status="waiting_approval",
            )
            if not waiting_todo:
                return
            self.repo.transition_run_status(
                run["id"],
                from_statuses=["in_progress"],
                to_status="waiting_approval",
                active_todo_id=todo["id"],
            )
            self.repo.append_event(
                run["id"],
                "approval_required",
                {
                    "todo_id": todo["id"],
                    "beat_id": beat["id"],
                    "fragment_id": fragment["id"],
                    "task_type": todo["task_type"],
                },
            )

    async def _generate_text(
        self,
        task_type: str,
        beat_summary: str,
        chapter_title: str,
        run: dict[str, Any],
        beat: dict[str, Any],
    ) -> str:
        if task_type == "draft_scene":
            return await self.provider.draft_scene(beat_summary, chapter_title)
        if task_type == "polish_transition":
            return await self.provider.polish_transition(beat_summary, chapter_title)
        if task_type == "revise_scene":
            latest = self.repo.latest_fragment(run["id"], beat["id"])
            source = latest.get("text", "")
            notes = "revise for flow and clarity"
            return await self.provider.revise_scene(source, notes, beat_summary, chapter_title)
        raise ValueError(f"Unsupported task type: {task_type}")

    async def handle_action(self, run_id: int, action: str, beat_id: int | None = None, notes: str | None = None) -> dict[str, Any]:
        run = self.repo.get_run(run_id)
        if not run:
            raise ValueError(f"Run {run_id} does not exist")

        if action == "lock_beat":
            if beat_id is None:
                raise ValueError("lock_beat requires beat_id")
            beat = self.repo.lock_beat(beat_id)
            self.repo.append_event(run_id, "beat_locked", {"beat_id": beat_id, "at": utc_now()})
            return {"beat": beat}

        if action == "pause":
            if run["status"] not in PAUSABLE_RUN_STATUSES:
                raise ValueError(f"Cannot pause run in status {run['status']}")
            paused = self.repo.transition_run_status(
                run_id,
                from_statuses=list(PAUSABLE_RUN_STATUSES),
                to_status="paused",
            )
            if not paused:
                latest = self.repo.get_run(run_id)
                raise ValueError(f"Cannot pause run in status {latest.get('status')}")
            self._update_checkpoint(run_id)
            self.repo.append_event(run_id, "run_paused", {"at": utc_now()})
            return self.repo.get_run(run_id)

        if action == "resume":
            if run["status"] != "paused":
                raise ValueError("Run is not paused.")
            todo = self.repo.get_todo(run.get("active_todo_id")) if run.get("active_todo_id") else {}
            resumed_status = "waiting_approval" if todo and todo.get("status") == "waiting_approval" else "in_progress"
            resumed = self.repo.transition_run_status(
                run_id,
                from_statuses=["paused"],
                to_status=resumed_status,
            )
            if not resumed:
                raise ValueError("Run is no longer paused.")
            self.repo.append_event(run_id, "run_resumed", {"at": utc_now()})
            if resumed_status == "in_progress":
                self.dispatch_run(run_id)
            return self.repo.get_run(run_id)

        if action == "cancel":
            self.repo.cancel_pending_todos(run_id)
            self.repo.update_run(run_id, status="canceled", ended=True)
            self.repo.append_event(run_id, "run_canceled", {"at": utc_now()})
            return self.repo.get_run(run_id)

        if action == "approve":
            todo_id = run.get("active_todo_id")
            if not todo_id:
                raise ValueError("No active todo to approve.")
            todo = self.repo.get_todo(todo_id)
            if todo.get("status") != "waiting_approval":
                raise ValueError("Active todo is not waiting for approval.")
            latest = self.repo.latest_fragment(run_id, todo["beat_id"])
            if not latest:
                raise ValueError("No draft fragment available to approve.")
            approved = self.repo.approve_fragment(latest["id"])
            todo_done = self.repo.transition_todo_status(
                todo_id,
                from_statuses=["waiting_approval"],
                to_status="done",
            )
            if not todo_done:
                raise ValueError("Active todo is no longer waiting for approval.")
            transitioned = self.repo.transition_run_status(
                run_id,
                from_statuses=["waiting_approval", "paused"],
                to_status="in_progress",
            )
            if not transitioned:
                raise ValueError("Run cannot be approved from its current status.")
            self.repo.append_event(
                run_id,
                "fragment_approved",
                {"todo_id": todo_id, "fragment_id": approved["id"], "at": utc_now()},
            )
            self.dispatch_run(run_id)
            return self.repo.get_run(run_id)

        if action == "revise":
            todo_id = run.get("active_todo_id")
            if not todo_id:
                raise ValueError("No active todo to revise.")
            todo = self.repo.get_todo(todo_id)
            if todo.get("status") != "waiting_approval":
                raise ValueError("Active todo is not waiting for revision.")
            beat = self.repo.get_beat(todo["beat_id"])
            chapter = self.repo.get_chapter(run["chapter_id"])
            latest = self.repo.latest_fragment(run_id, beat["id"])
            revised = await self.provider.revise_scene(
                current_text=latest.get("text", ""),
                notes=notes or "tighten voice and sharpen intent",
                beat_summary=beat["summary"],
                chapter_title=chapter["title"],
            )
            fragment = self.repo.create_fragment(
                chapter_id=chapter["id"],
                beat_id=beat["id"],
                run_id=run_id,
                text=revised,
                approved_flag=0,
            )
            revised_todo = self.repo.transition_todo_status(
                todo_id,
                from_statuses=["waiting_approval"],
                to_status="waiting_approval",
                attempt_delta=1,
                notes=notes,
            )
            if not revised_todo:
                raise ValueError("Active todo is no longer waiting for revision.")
            self.repo.transition_run_status(
                run_id,
                from_statuses=["waiting_approval", "paused"],
                to_status="waiting_approval",
            )
            self.repo.append_event(
                run_id,
                "fragment_revised",
                {"todo_id": todo_id, "fragment_id": fragment["id"], "notes": notes or ""},
            )
            self._update_checkpoint(run_id)
            return self.repo.get_run(run_id)

        raise ValueError(f"Unsupported action: {action}")

    def _update_checkpoint(self, run_id: int) -> None:
        todos = self.repo.list_todos(run_id)
        completed = len([item for item in todos if item["status"] == "done"])
        waiting = len([item for item in todos if item["status"] == "waiting_approval"])
        checkpoint = {
            "total_todos": len(todos),
            "completed_todos": completed,
            "waiting_todos": waiting,
            "updated_at": utc_now(),
        }
        run = self.repo.get_run(run_id)
        if run.get("active_todo_id"):
            checkpoint["active_todo_id"] = run["active_todo_id"]
        self.repo.update_run_checkpoint(run_id, checkpoint)

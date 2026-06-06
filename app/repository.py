from __future__ import annotations

import json
from typing import Any

from app.db import Database


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


UNSET = object()


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # Chapters and beats
    def create_chapter(self, title: str, canonical_path: str, status: str = "draft") -> dict[str, Any]:
        chapter_id = self.db.execute(
            """
            INSERT INTO chapters (title, canonical_path, status)
            VALUES (?, ?, ?)
            """,
            (title, canonical_path, status),
        )
        return self.get_chapter(chapter_id)

    def list_chapters(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM chapters ORDER BY id DESC")
        return [_row_to_dict(row) for row in rows]

    def get_chapter(self, chapter_id: int) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM chapters WHERE id = ?", (chapter_id,))
        return _row_to_dict(row)

    def get_chapter_by_canonical_path(self, canonical_path: str) -> dict[str, Any]:
        row = self.db.fetchone(
            "SELECT * FROM chapters WHERE canonical_path = ? ORDER BY id DESC LIMIT 1",
            (canonical_path,),
        )
        return _row_to_dict(row)

    def update_chapter(
        self,
        chapter_id: int,
        *,
        title: str | object = UNSET,
        canonical_path: str | object = UNSET,
    ) -> dict[str, Any]:
        current = self.get_chapter(chapter_id)
        if not current:
            return {}
        if title is UNSET:
            title = current["title"]
        if canonical_path is UNSET:
            canonical_path = current["canonical_path"]
        self.db.execute(
            """
            UPDATE chapters
            SET title = ?, canonical_path = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (title, canonical_path, chapter_id),
        )
        return self.get_chapter(chapter_id)

    def set_chapter_status(self, chapter_id: int, status: str) -> None:
        self.db.execute(
            """
            UPDATE chapters
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, chapter_id),
        )

    def replace_beats(self, chapter_id: int, beats: list[str]) -> list[dict[str, Any]]:
        self.db.execute("DELETE FROM beats WHERE chapter_id = ?", (chapter_id,))
        rows: list[tuple[int, int, str, str]] = []
        for idx, beat in enumerate(beats, start=1):
            rows.append((chapter_id, idx, beat.strip(), "draft"))
        if rows:
            self.db.executemany(
                """
                INSERT INTO beats (chapter_id, beat_order, summary, status)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
        self.set_chapter_status(chapter_id, "spine_draft")
        return self.list_beats(chapter_id)

    def list_beats(self, chapter_id: int, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.db.fetchall(
                """
                SELECT * FROM beats
                WHERE chapter_id = ? AND status = ?
                ORDER BY beat_order ASC
                """,
                (chapter_id, status),
            )
        else:
            rows = self.db.fetchall(
                "SELECT * FROM beats WHERE chapter_id = ? ORDER BY beat_order ASC", (chapter_id,)
            )
        return [_row_to_dict(row) for row in rows]

    def get_beat(self, beat_id: int) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM beats WHERE id = ?", (beat_id,))
        return _row_to_dict(row)

    def lock_beat(self, beat_id: int) -> dict[str, Any]:
        self.db.execute(
            """
            UPDATE beats
            SET status = 'locked', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (beat_id,),
        )
        return self.get_beat(beat_id)

    def lock_all_beats(self, chapter_id: int) -> list[dict[str, Any]]:
        self.db.execute(
            """
            UPDATE beats
            SET status = 'locked', updated_at = CURRENT_TIMESTAMP
            WHERE chapter_id = ?
            """,
            (chapter_id,),
        )
        self.set_chapter_status(chapter_id, "spine_locked")
        return self.list_beats(chapter_id)

    # Runs
    def create_run(self, chapter_id: int, mode: str) -> dict[str, Any]:
        run_id = self.db.execute(
            """
            INSERT INTO runs (chapter_id, mode, status, checkpoint_json, started_at)
            VALUES (?, ?, 'queued', '{}', CURRENT_TIMESTAMP)
            """,
            (chapter_id, mode),
        )
        return self.get_run(run_id)

    def list_runs_by_statuses(self, statuses: list[str]) -> list[dict[str, Any]]:
        if not statuses:
            return []
        placeholders = ", ".join(["?"] * len(statuses))
        rows = self.db.fetchall(
            f"""
            SELECT * FROM runs
            WHERE status IN ({placeholders})
            ORDER BY id ASC
            """,
            tuple(statuses),
        )
        runs: list[dict[str, Any]] = []
        for row in rows:
            run = _row_to_dict(row)
            try:
                run["checkpoint_json"] = json.loads(run.get("checkpoint_json") or "{}")
            except json.JSONDecodeError:
                run["checkpoint_json"] = {}
            runs.append(run)
        return runs

    def get_run(self, run_id: int) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
        data = _row_to_dict(row)
        if not data:
            return {}
        try:
            data["checkpoint_json"] = json.loads(data["checkpoint_json"] or "{}")
        except json.JSONDecodeError:
            data["checkpoint_json"] = {}
        data["event_count"] = self.count_run_events(run_id)
        return data

    def update_run(
        self,
        run_id: int,
        *,
        status: str | None = None,
        active_todo_id: int | None | object = UNSET,
        checkpoint: dict[str, Any] | None = None,
        last_error: str | None | object = UNSET,
        ended: bool = False,
    ) -> dict[str, Any]:
        current = self.get_run(run_id)
        if not current:
            return {}
        status = status or current["status"]
        if active_todo_id is UNSET:
            active_todo_id = current.get("active_todo_id")
        checkpoint_blob = current.get("checkpoint_json", {})
        if checkpoint is not None:
            checkpoint_blob = checkpoint
        if last_error is UNSET:
            last_error = current.get("last_error")
        ended_at = "CURRENT_TIMESTAMP" if ended else "ended_at"
        query = (
            "UPDATE runs SET status = ?, active_todo_id = ?, checkpoint_json = ?, last_error = ?, "
            "ended_at = {ended_at}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        ).format(ended_at=ended_at)
        self.db.execute(
            query,
            (
                status,
                active_todo_id,
                json.dumps(checkpoint_blob),
                last_error,
                run_id,
            ),
        )
        return self.get_run(run_id)

    def transition_run_status(
        self,
        run_id: int,
        *,
        from_statuses: list[str] | tuple[str, ...] | set[str],
        to_status: str,
        active_todo_id: int | None | object = UNSET,
        checkpoint: dict[str, Any] | object = UNSET,
        last_error: str | None | object = UNSET,
        ended: bool = False,
    ) -> dict[str, Any]:
        current = self.get_run(run_id)
        if not current:
            return {}

        statuses = list(dict.fromkeys(from_statuses))
        if not statuses:
            return {}
        if current.get("status") not in statuses:
            return {}

        if active_todo_id is UNSET:
            active_todo_id = current.get("active_todo_id")

        checkpoint_blob = current.get("checkpoint_json", {})
        if checkpoint is not UNSET:
            checkpoint_blob = checkpoint

        if last_error is UNSET:
            last_error = current.get("last_error")

        placeholders = ", ".join(["?"] * len(statuses))
        ended_at = "CURRENT_TIMESTAMP" if ended else "ended_at"
        query = (
            "UPDATE runs SET status = ?, active_todo_id = ?, checkpoint_json = ?, last_error = ?, "
            "ended_at = {ended_at}, updated_at = CURRENT_TIMESTAMP "
            f"WHERE id = ? AND status IN ({placeholders})"
        ).format(ended_at=ended_at)
        self.db.execute(
            query,
            (
                to_status,
                active_todo_id,
                json.dumps(checkpoint_blob),
                last_error,
                run_id,
                *statuses,
            ),
        )
        updated = self.get_run(run_id)
        if updated and updated.get("status") == to_status:
            return updated
        return {}

    def update_run_checkpoint(self, run_id: int, checkpoint: dict[str, Any]) -> dict[str, Any]:
        current = self.get_run(run_id)
        if not current:
            return {}
        self.db.execute(
            """
            UPDATE runs
            SET checkpoint_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (json.dumps(checkpoint), run_id),
        )
        return self.get_run(run_id)

    # Todo items
    def create_todo(self, run_id: int, beat_id: int, task_type: str, notes: str | None = None) -> dict[str, Any]:
        todo_id = self.db.execute(
            """
            INSERT INTO todo_items (run_id, beat_id, task_type, status, notes)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (run_id, beat_id, task_type, notes),
        )
        return self.get_todo(todo_id)

    def seed_todos_for_run(self, run_id: int, beat_ids: list[int]) -> None:
        rows: list[tuple[int, int, str, str]] = []
        for idx, beat_id in enumerate(beat_ids):
            rows.append((run_id, beat_id, "draft_scene", "pending"))
            if idx < len(beat_ids) - 1:
                rows.append((run_id, beat_id, "polish_transition", "pending"))
        if not rows:
            return
        self.db.executemany(
            """
            INSERT INTO todo_items (run_id, beat_id, task_type, status)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )

    def list_todos(self, run_id: int) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT * FROM todo_items
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        )
        return [_row_to_dict(row) for row in rows]

    def count_todos(self, run_id: int) -> int:
        row = self.db.fetchone("SELECT COUNT(*) AS n FROM todo_items WHERE run_id = ?", (run_id,))
        return int(row["n"]) if row else 0

    def next_pending_todo(self, run_id: int) -> dict[str, Any]:
        row = self.db.fetchone(
            """
            SELECT * FROM todo_items
            WHERE run_id = ? AND status = 'pending'
            ORDER BY id ASC
            LIMIT 1
            """,
            (run_id,),
        )
        return _row_to_dict(row)

    def get_todo(self, todo_id: int) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM todo_items WHERE id = ?", (todo_id,))
        return _row_to_dict(row)

    def update_todo(
        self,
        todo_id: int,
        *,
        status: str | None = None,
        attempt_delta: int = 0,
        notes: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_todo(todo_id)
        if not current:
            return {}
        status = status or current["status"]
        attempt_count = int(current["attempt_count"]) + int(attempt_delta)
        notes = notes if notes is not None else current.get("notes")
        self.db.execute(
            """
            UPDATE todo_items
            SET status = ?, attempt_count = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, attempt_count, notes, todo_id),
        )
        return self.get_todo(todo_id)

    def transition_todo_status(
        self,
        todo_id: int,
        *,
        from_statuses: list[str] | tuple[str, ...] | set[str],
        to_status: str,
        attempt_delta: int = 0,
        notes: str | None | object = UNSET,
    ) -> dict[str, Any]:
        current = self.get_todo(todo_id)
        if not current:
            return {}

        statuses = list(dict.fromkeys(from_statuses))
        if not statuses:
            return {}
        if current.get("status") not in statuses:
            return {}

        attempt_count = int(current["attempt_count"]) + int(attempt_delta)
        if notes is UNSET:
            notes = current.get("notes")

        placeholders = ", ".join(["?"] * len(statuses))
        self.db.execute(
            f"""
            UPDATE todo_items
            SET status = ?, attempt_count = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ({placeholders})
            """,
            (to_status, attempt_count, notes, todo_id, *statuses),
        )
        updated = self.get_todo(todo_id)
        if updated and updated.get("status") == to_status:
            return updated
        return {}

    def cancel_pending_todos(self, run_id: int) -> None:
        self.db.execute(
            """
            UPDATE todo_items
            SET status = 'canceled', updated_at = CURRENT_TIMESTAMP
            WHERE run_id = ? AND status IN ('pending', 'in_progress', 'waiting_approval')
            """,
            (run_id,),
        )

    def reset_in_progress_todos_to_pending(self, run_id: int) -> None:
        self.db.execute(
            """
            UPDATE todo_items
            SET status = 'pending', updated_at = CURRENT_TIMESTAMP
            WHERE run_id = ? AND status = 'in_progress'
            """,
            (run_id,),
        )

    # Draft fragments
    def create_fragment(
        self,
        chapter_id: int,
        beat_id: int,
        run_id: int,
        text: str,
        approved_flag: int,
    ) -> dict[str, Any]:
        row = self.db.fetchone(
            """
            SELECT COALESCE(MAX(version), 0) AS v
            FROM draft_fragments
            WHERE run_id = ? AND beat_id = ?
            """,
            (run_id, beat_id),
        )
        next_version = int(row["v"]) + 1 if row else 1
        fragment_id = self.db.execute(
            """
            INSERT INTO draft_fragments (chapter_id, beat_id, run_id, version, text, approved_flag)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chapter_id, beat_id, run_id, next_version, text, approved_flag),
        )
        return self.get_fragment(fragment_id)

    def get_fragment(self, fragment_id: int) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM draft_fragments WHERE id = ?", (fragment_id,))
        return _row_to_dict(row)

    def latest_fragment(self, run_id: int, beat_id: int) -> dict[str, Any]:
        row = self.db.fetchone(
            """
            SELECT * FROM draft_fragments
            WHERE run_id = ? AND beat_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (run_id, beat_id),
        )
        return _row_to_dict(row)

    def approve_fragment(self, fragment_id: int) -> dict[str, Any]:
        fragment = self.get_fragment(fragment_id)
        if not fragment:
            return {}
        self.db.execute(
            """
            UPDATE draft_fragments
            SET approved_flag = 0
            WHERE run_id = ? AND beat_id = ?
            """,
            (fragment["run_id"], fragment["beat_id"]),
        )
        self.db.execute(
            "UPDATE draft_fragments SET approved_flag = 1 WHERE id = ?",
            (fragment_id,),
        )
        return self.get_fragment(fragment_id)

    def assemble_preview(self, chapter_id: int) -> str:
        beats = self.list_beats(chapter_id)
        chunks: list[str] = []
        for beat in beats:
            row = self.db.fetchone(
                """
                SELECT text
                FROM draft_fragments
                WHERE chapter_id = ? AND beat_id = ? AND approved_flag = 1
                ORDER BY id DESC
                LIMIT 1
                """,
                (chapter_id, beat["id"]),
            )
            if row and row["text"]:
                chunks.append(row["text"])
        return "\n\n".join(chunks)

    # Integration source configs
    def upsert_integration_source_config(self, source: str, config: dict[str, Any]) -> dict[str, Any]:
        source = source.lower().strip()
        self.db.execute(
            """
            INSERT INTO integration_sources (source, config_json)
            VALUES (?, ?)
            ON CONFLICT(source) DO UPDATE
            SET config_json = excluded.config_json, updated_at = CURRENT_TIMESTAMP
            """,
            (source, json.dumps(config)),
        )
        return self.get_integration_source_config(source)

    def get_integration_source_config(self, source: str) -> dict[str, Any]:
        row = self.db.fetchone(
            "SELECT * FROM integration_sources WHERE source = ?",
            (source.lower().strip(),),
        )
        data = _row_to_dict(row)
        if not data:
            return {}
        try:
            data["config"] = json.loads(data.get("config_json") or "{}")
        except json.JSONDecodeError:
            data["config"] = {}
        return data

    def list_integration_source_configs(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM integration_sources ORDER BY source ASC")
        items: list[dict[str, Any]] = []
        for row in rows:
            data = _row_to_dict(row)
            try:
                data["config"] = json.loads(data.get("config_json") or "{}")
            except json.JSONDecodeError:
                data["config"] = {}
            items.append(data)
        return items

    # Run events
    def append_event(self, run_id: int, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = self.db.execute(
            """
            INSERT INTO run_events (run_id, event_type, payload_json)
            VALUES (?, ?, ?)
            """,
            (run_id, event_type, json.dumps(payload)),
        )
        row = self.db.fetchone("SELECT * FROM run_events WHERE id = ?", (event_id,))
        event = _row_to_dict(row)
        event["payload"] = payload
        return event

    def list_events(self, run_id: int, after_id: int = 0) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT * FROM run_events
            WHERE run_id = ? AND id > ?
            ORDER BY id ASC
            """,
            (run_id, after_id),
        )
        events: list[dict[str, Any]] = []
        for row in rows:
            raw = _row_to_dict(row)
            raw["payload"] = json.loads(raw.get("payload_json", "{}"))
            events.append(raw)
        return events

    def count_run_events(self, run_id: int) -> int:
        row = self.db.fetchone("SELECT COUNT(*) AS n FROM run_events WHERE run_id = ?", (run_id,))
        return int(row["n"]) if row else 0

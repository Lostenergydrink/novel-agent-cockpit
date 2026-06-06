from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chapters (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  canonical_path TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'draft',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS beats (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                  beat_order INTEGER NOT NULL,
                  summary TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'draft',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                  mode TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'queued',
                  active_todo_id INTEGER,
                  checkpoint_json TEXT NOT NULL DEFAULT '{}',
                  last_error TEXT,
                  started_at TEXT,
                  ended_at TEXT,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS todo_items (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                  beat_id INTEGER NOT NULL REFERENCES beats(id) ON DELETE CASCADE,
                  task_type TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending',
                  attempt_count INTEGER NOT NULL DEFAULT 0,
                  notes TEXT,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS draft_fragments (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                  beat_id INTEGER NOT NULL REFERENCES beats(id) ON DELETE CASCADE,
                  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                  version INTEGER NOT NULL,
                  text TEXT NOT NULL,
                  approved_flag INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS run_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                  event_type TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS integration_sources (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  source TEXT NOT NULL UNIQUE,
                  config_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.commit()

    def execute(self, query: str, params: tuple = ()) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(query, params)
            conn.commit()
            return int(cur.lastrowid)

    def executemany(self, query: str, seq_of_params: list[tuple]) -> None:
        with self._lock, self._connect() as conn:
            conn.executemany(query, seq_of_params)
            conn.commit()

    def fetchone(self, query: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock, self._connect() as conn:
            cur = conn.execute(query, params)
            return cur.fetchone()

    def fetchall(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(query, params)
            return list(cur.fetchall())

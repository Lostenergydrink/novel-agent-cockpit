from __future__ import annotations

from typing import Literal

RunMode = Literal["fast", "section"]
RunStatus = Literal[
    "queued",
    "in_progress",
    "waiting_revision",
    "waiting_approval",
    "paused",
    "completed",
    "failed",
    "canceled",
]
BeatStatus = Literal["draft", "revise", "locked"]
TodoStatus = Literal["pending", "in_progress", "waiting_approval", "done", "canceled"]
TodoTaskType = Literal["draft_scene", "revise_scene", "polish_transition"]

TERMINAL_RUN_STATUSES = {"completed", "failed", "canceled"}
PAUSABLE_RUN_STATUSES = {"in_progress", "waiting_approval"}


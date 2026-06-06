from __future__ import annotations

from typing import Protocol


class WritingProvider(Protocol):
    name: str

    async def brainstorm(self, message: str, chapter_title: str | None = None) -> str:
        ...

    async def draft_scene(self, beat_summary: str, chapter_title: str) -> str:
        ...

    async def revise_scene(self, current_text: str, notes: str, beat_summary: str, chapter_title: str) -> str:
        ...

    async def polish_transition(self, beat_summary: str, chapter_title: str) -> str:
        ...


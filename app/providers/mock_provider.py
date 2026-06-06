from __future__ import annotations

from app.providers.base import WritingProvider


class MockWritingProvider(WritingProvider):
    name = "mock"

    async def brainstorm(self, message: str, chapter_title: str | None = None) -> str:
        chapter_line = f' for "{chapter_title}"' if chapter_title else ""
        return (
            f"Brainstorm riff{chapter_line}: {message}\n"
            "1) clarify desired emotional turn\n"
            "2) anchor POV pressure in this beat\n"
            "3) lock one concrete image to carry into prose"
        )

    async def draft_scene(self, beat_summary: str, chapter_title: str) -> str:
        return (
            f"[{chapter_title}] Draft Scene\n"
            f"Beat target: {beat_summary}\n"
            "The scene opens with immediate pressure and a concrete sensory anchor."
        )

    async def revise_scene(self, current_text: str, notes: str, beat_summary: str, chapter_title: str) -> str:
        _ = current_text
        return (
            f"[{chapter_title}] Revised Scene\n"
            f"Beat target: {beat_summary}\n"
            f"Revision note: {notes}\n"
            "Updated draft follows with tightened cadence and clearer intent."
        )

    async def polish_transition(self, beat_summary: str, chapter_title: str) -> str:
        return (
            f"[{chapter_title}] Transition Polish\n"
            f"Bridge from beat: {beat_summary}\n"
            "The transition carries motive and tension cleanly into the next beat."
        )

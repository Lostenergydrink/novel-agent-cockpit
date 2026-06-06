from __future__ import annotations

from textwrap import dedent

from app.providers.base import WritingProvider


class OpenAIWritingProvider(WritingProvider):
    name = "openai"

    def __init__(self, *, api_key: str, model: str) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self.client = AsyncOpenAI(api_key=api_key)

    async def _respond(self, system_prompt: str, user_prompt: str) -> str:
        response = await self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (response.output_text or "").strip()
        return text if text else "No text returned."

    async def brainstorm(self, message: str, chapter_title: str | None = None) -> str:
        chapter = chapter_title or "working chapter"
        return await self._respond(
            dedent(
                """
                You are a fiction development collaborator.
                Keep output practical and organized for novel planning.
                """
            ).strip(),
            f'Chapter context: "{chapter}"\nUser brainstorm request: {message}',
        )

    async def draft_scene(self, beat_summary: str, chapter_title: str) -> str:
        return await self._respond(
            "Write a focused novel scene draft aligned to the specified beat.",
            f'Chapter: "{chapter_title}"\nBeat: {beat_summary}',
        )

    async def revise_scene(self, current_text: str, notes: str, beat_summary: str, chapter_title: str) -> str:
        return await self._respond(
            "Revise the scene while preserving continuity and narrative intent.",
            (
                f'Chapter: "{chapter_title}"\nBeat: {beat_summary}\n'
                f"Revision notes: {notes}\n\nCurrent draft:\n{current_text}"
            ),
        )

    async def polish_transition(self, beat_summary: str, chapter_title: str) -> str:
        return await self._respond(
            "Write a short transition paragraph that sets up the next beat.",
            f'Chapter: "{chapter_title}"\nCurrent beat: {beat_summary}',
        )


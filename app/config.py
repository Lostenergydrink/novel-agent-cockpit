from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(slots=True)
class Settings:
    db_path: Path
    workspace_root: Path
    openai_api_key: str | None
    openai_model: str
    provider_preference: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    raw_db = os.getenv("COCKPIT_DB_PATH", "data/cockpit.db")
    db_path = Path(raw_db)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    workspace_raw = os.getenv("WORKSPACE_ROOT", str(Path.cwd()))
    workspace_root = Path(workspace_raw).resolve()

    return Settings(
        db_path=db_path,
        workspace_root=workspace_root,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        provider_preference=os.getenv("MODEL_PROVIDER", "openai"),
    )


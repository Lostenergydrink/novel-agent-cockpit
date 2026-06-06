from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChapterCreateRequest(BaseModel):
    title: str = Field(min_length=1)
    canonical_path: str = Field(min_length=1)


class ChapterUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    canonical_path: str | None = Field(default=None, min_length=1)


class BeatsReplaceRequest(BaseModel):
    beats: list[str] = Field(min_length=1)


class ManuscriptUploadRequest(BaseModel):
    filename: str = Field(min_length=1)
    content: str
    chapter_title: str | None = Field(default=None, min_length=1)
    overwrite: bool = False


class ManuscriptLinkRequest(BaseModel):
    canonical_path: str = Field(min_length=1)
    chapter_title: str | None = Field(default=None, min_length=1)


class ChapterPublishRequest(BaseModel):
    overwrite: bool = True
    dry_run: bool = False
    backup: bool = True


class RunCreateRequest(BaseModel):
    chapter_id: int
    mode: Literal["fast", "section"]


class RunActionRequest(BaseModel):
    action: Literal["revise", "lock_beat", "approve", "pause", "resume", "cancel"]
    beat_id: int | None = None
    notes: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    chapter_id: int | None = None


class ChatResponse(BaseModel):
    reply: str
    provider: str


class IntegrationQueryRequest(BaseModel):
    source: Literal["repo", "notion", "mcp", "notebooklm"]
    query: str = Field(min_length=1)


class IntegrationWriteRequest(BaseModel):
    source: Literal["repo", "notion", "mcp", "notebooklm"]
    operation: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class IntegrationSourceConfigRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class IntegrationConfigUploadRequest(BaseModel):
    filename: str = Field(min_length=1)
    content: str = Field(min_length=2)


class RepoSourceConfig(BaseModel):
    root: str = Field(min_length=1)
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)


class NotionSourceConfig(BaseModel):
    workspace_id: str = Field(min_length=1)
    token_env: str = Field(min_length=1)
    database_ids: list[str] = Field(default_factory=list)


class MCPQueryProfile(BaseModel):
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    argument_map: dict[str, Any] = Field(default_factory=dict)
    query_arg: str | None = Field(default=None, min_length=1)


class MCPSourceConfig(BaseModel):
    server: str = Field(min_length=1)
    workspace: str | None = Field(default=None, min_length=1)
    toolset: str | None = Field(default=None, min_length=1)
    query_profile: MCPQueryProfile | None = None
    profiles: dict[str, MCPQueryProfile] = Field(default_factory=dict)


class NotebookLMSourceConfig(BaseModel):
    notebook_id: str = Field(min_length=1)
    project: str | None = Field(default=None, min_length=1)
    mcp_server: str | None = Field(default=None, min_length=1)
    server: str | None = Field(default=None, min_length=1)
    toolset: str | None = Field(default=None, min_length=1)
    query_profile: MCPQueryProfile | None = None
    profiles: dict[str, MCPQueryProfile] = Field(default_factory=dict)

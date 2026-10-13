from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


def literal_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port == 0
        or any(char in value for char in "{} \t\r\n")
    ):
        raise ValueError("API base URL must be a literal HTTP(S) URL")
    return value


BaseURL = Annotated[str, Field(min_length=1, max_length=2048), AfterValidator(literal_base_url)]
ProjectName = Annotated[str, Field(min_length=1, max_length=100)]


class ProjectModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectCreate(ProjectModel):
    name: ProjectName
    description: str = Field(default="", max_length=2000)
    api_base_url: BaseURL | None = None


class ProjectUpdate(ProjectModel):
    name: ProjectName | None = None
    description: str | None = Field(default=None, max_length=2000)
    api_base_url: BaseURL | None = None

    @model_validator(mode="after")
    def valid_update(self) -> "ProjectUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one project field is required")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Name cannot be null")
        if "description" in self.model_fields_set and self.description is None:
            raise ValueError("Description cannot be null")
        return self


class Project(ProjectCreate):
    id: UUID
    status: Literal["active", "deleting"]
    created_at: AwareDatetime


class FileMetadata(ProjectModel):
    id: UUID
    project_id: UUID
    original_name: str = Field(min_length=1, max_length=255)
    kind: Literal["spec", "code", "note"]
    language: Literal["typescript", "javascript", "python"] | None = None
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=1, le=2 * 1024**2)
    state: Literal["stored", "deleting"]

    @model_validator(mode="after")
    def valid_language(self) -> "FileMetadata":
        if (self.kind == "code") != (self.language is not None):
            raise ValueError("Only code files must have a language")
        return self


class RevisionCreate(ProjectModel):
    old_spec_id: UUID
    new_spec_id: UUID
    code_ids: list[UUID] = Field(min_length=1, max_length=20)
    note_ids: list[UUID] = Field(default_factory=list, max_length=10)
    parent_revision_id: UUID | None = None

    @model_validator(mode="after")
    def unique_files(self) -> "RevisionCreate":
        ids = [self.old_spec_id, self.new_spec_id, *self.code_ids, *self.note_ids]
        if len(ids) != len(set(ids)):
            raise ValueError("Each file ID must have exactly one revision role")
        return self


class Revision(RevisionCreate):
    id: UUID
    project_id: UUID
    api_base_url: BaseURL | None
    created_at: AwareDatetime
    frozen_at: AwareDatetime | None = None


class FileUpload(ProjectModel):
    kind: Literal["spec", "code", "note"]

import re
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.modules.projects.schemas import BaseURL


def canonical_operation(value: str) -> str:
    if not re.fullmatch(r"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE) /[^\s?#]*", value):
        raise ValueError("Operation key must be canonical METHOD /path")
    path = value.split(" ", 1)[1]
    if not re.fullmatch(r"(?:[^{}]|\{[^{} /]+\})*", path):
        raise ValueError("Operation path contains invalid parameters")
    return value


OperationKey = Annotated[str, Field(max_length=2048), AfterValidator(canonical_operation)]
EvidenceID = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]
Text = Annotated[str, Field(min_length=1, max_length=4000)]
Language = Literal["typescript", "javascript", "python"]


class AnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceSpan(AnalysisModel):
    byte_start: int = Field(ge=0)
    byte_end: int = Field(ge=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered_span(self) -> "SourceSpan":
        if self.byte_end <= self.byte_start or self.line_end < self.line_start:
            raise ValueError("Source spans must be ordered, nonempty UTF-8 byte ranges")
        return self


class CallSite(AnalysisModel):
    id: EvidenceID
    file_id: UUID
    language: Language
    span: SourceSpan
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"] | None
    url_pattern: str | None = Field(max_length=2048)
    candidate_operation_keys: list[OperationKey] = Field(default_factory=list, max_length=100)
    operation_key: OperationKey | None = None
    mapping_source: Literal["automatic", "manual"] | None = None
    unresolved_reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def consistent_mapping(self) -> "CallSite":
        if len(self.candidate_operation_keys) != len(set(self.candidate_operation_keys)):
            raise ValueError("Candidate operation keys must be unique")
        if self.operation_key is None:
            if self.mapping_source is not None or not self.unresolved_reason:
                raise ValueError("Unmapped calls require an unresolved reason")
        elif self.mapping_source is None or self.unresolved_reason is not None:
            raise ValueError("Mapped calls require a source and no unresolved reason")
        return self


class ContractLocation(AnalysisModel):
    file_id: UUID
    json_pointer: str = Field(max_length=4000, pattern=r"^(?:|/(?:[^~]|~[01])*)$")
    precision: Literal["field", "operation", "component", "document"]


class Finding(AnalysisModel):
    id: EvidenceID
    operation_key: OperationKey | None = None
    severity: Literal["breaking", "nonbreaking", "unknown"]
    change_type: str = Field(min_length=1, max_length=100)
    message: Text
    old_location: ContractLocation | None = None
    new_location: ContractLocation | None = None

    @model_validator(mode="after")
    def has_source(self) -> "Finding":
        if self.old_location is None and self.new_location is None:
            raise ValueError("Finding requires a verified source location")
        return self


class SpanEvidence(AnalysisModel):
    id: EvidenceID
    kind: Literal["code", "note"]
    file_id: UUID
    span: SourceSpan
    heading: str | None = Field(default=None, max_length=500)
    character_start: int | None = Field(default=None, ge=0)
    character_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def ordered_characters(self) -> "SpanEvidence":
        if (self.character_start is None) != (self.character_end is None):
            raise ValueError("Character bounds must be provided together")
        if (
            self.character_start is not None
            and self.character_end is not None
            and self.character_end <= self.character_start
        ):
            raise ValueError("Character bounds must be ordered")
        return self


class ContractEvidence(AnalysisModel):
    id: EvidenceID
    kind: Literal["contract"]
    location: ContractLocation


Evidence = Annotated[SpanEvidence | ContractEvidence, Field(discriminator="kind")]


class Coverage(AnalysisModel):
    analyzed_file_ids: list[UUID] = Field(max_length=20)
    skipped_file_ids: list[UUID] = Field(default_factory=list, max_length=20)
    recognized_calls: int = Field(ge=0)
    mapped_calls: int = Field(ge=0)
    unresolved_candidates: int = Field(ge=0)
    parse_errors: list[Text] = Field(default_factory=list, max_length=20)
    limitations: list[Text] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def counts_match(self) -> "Coverage":
        if self.mapped_calls + self.unresolved_candidates != self.recognized_calls:
            raise ValueError("Mapped and unresolved counts must equal recognized calls")
        ids = [*self.analyzed_file_ids, *self.skipped_file_ids]
        if len(ids) > 20 or len(ids) != len(set(ids)):
            raise ValueError("Coverage files must be distinct and bounded")
        return self


class Preview(AnalysisModel):
    revision_id: UUID
    engine_version: str = Field(min_length=1, max_length=100)
    parser_version: str = Field(min_length=1, max_length=100)
    findings: list[Finding] = Field(max_length=10000)
    callsites: list[CallSite] = Field(max_length=10000)
    coverage: Coverage
    evidence: list[Evidence] = Field(max_length=20000)


class ManualMapping(AnalysisModel):
    callsite_id: EvidenceID
    operation_key: OperationKey


class AnalysisRequest(AnalysisModel):
    preview_id: UUID
    selected_operation_keys: list[OperationKey] = Field(min_length=1, max_length=30)
    manual_mappings: list[ManualMapping] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def unique_scope(self) -> "AnalysisRequest":
        if len(self.selected_operation_keys) != len(set(self.selected_operation_keys)):
            raise ValueError("Selected operations must be unique")
        ids = [mapping.callsite_id for mapping in self.manual_mappings]
        if len(ids) != len(set(ids)):
            raise ValueError("A callsite can have only one manual mapping")
        return self


class Step(AnalysisModel):
    id: EvidenceID
    instruction: Text
    evidence_ids: list[EvidenceID] = Field(min_length=1, max_length=20)


class SuggestedCheck(AnalysisModel):
    instruction: Text
    proposed: Literal[True] = True
    evidence_ids: list[EvidenceID] = Field(default_factory=list, max_length=20)


class OperationGuidance(AnalysisModel):
    operation_key: OperationKey
    change_ids: list[EvidenceID] = Field(min_length=1, max_length=100)
    candidate_callsite_ids: list[EvidenceID] = Field(default_factory=list, max_length=1000)
    status: Literal["guidance_available", "needs_information", "unsupported"]
    steps: list[Step] = Field(default_factory=list, max_length=20)
    suggested_checks: list[SuggestedCheck] = Field(default_factory=list, max_length=20)
    assumptions: list[Text] = Field(default_factory=list, max_length=20)
    unknowns: list[Text] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_status(self) -> "OperationGuidance":
        if self.status == "guidance_available" and not self.steps:
            raise ValueError("Available guidance requires evidenced steps")
        if self.status != "guidance_available" and not self.unknowns:
            raise ValueError("Unavailable guidance must explain missing information")
        return self


class Guidance(AnalysisModel):
    revision_id: UUID
    operations: list[OperationGuidance] = Field(min_length=1, max_length=30)


class ReviewRequest(AnalysisModel):
    decision: Literal["accepted", "edited", "rejected"]
    edited_instruction: Text | None = None
    edited_evidence_ids: list[EvidenceID] | None = Field(default=None, min_length=1, max_length=20)
    expected_version: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_edit(self) -> "ReviewRequest":
        if self.decision == "edited" and not self.edited_instruction:
            raise ValueError("Edited recommendations require user-authored text")
        if self.decision != "edited" and (
            self.edited_instruction is not None or self.edited_evidence_ids is not None
        ):
            raise ValueError("Only edited recommendations accept replacement content")
        return self


class Review(AnalysisModel):
    recommendation_id: EvidenceID
    decision: Literal["accepted", "edited", "rejected"]
    original_instruction: Text
    edited_instruction: Text | None = None
    edited_evidence_ids: list[EvidenceID] | None = Field(default=None, max_length=20)
    version: int = Field(ge=1)
    updated_at: AwareDatetime


class RunError(AnalysisModel):
    code: str = Field(min_length=1, max_length=100)
    message: Text


class Run(AnalysisModel):
    id: UUID
    project_id: UUID
    revision_id: UUID
    preview_run_id: UUID | None = None
    kind: Literal["preview", "guidance"]
    state: Literal["queued", "running", "succeeded", "failed"]
    stage: str = Field(min_length=1, max_length=100)
    attempts: int = Field(ge=0, le=2)
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    quota_state: Literal["reserved", "consumed", "released"] | None = None
    coverage: Coverage | None = None
    preview: Preview | None = None
    guidance: Guidance | None = None
    error: RunError | None = None
    ai_cost_usd: Decimal = Field(default=Decimal("0"), ge=0)

    @model_validator(mode="after")
    def valid_kind(self) -> "Run":
        if self.kind == "preview" and (self.quota_state is not None or self.guidance is not None):
            raise ValueError("Previews cannot consume quota or contain generated guidance")
        if self.kind == "guidance" and (self.quota_state is None or self.preview_run_id is None):
            raise ValueError("Guidance requires a preview and quota disposition")
        if self.state != "succeeded" and self.guidance is not None:
            raise ValueError("Only successful runs can publish guidance")
        return self


class InputFile(AnalysisModel):
    id: UUID
    original_name: str = Field(min_length=1, max_length=255)
    role: Literal["old_spec", "new_spec", "code", "note"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class InputSnapshot(AnalysisModel):
    revision_id: UUID
    api_base_url: BaseURL | None
    files: list[InputFile] = Field(min_length=3, max_length=32)
    selected_operation_keys: list[OperationKey] = Field(default_factory=list, max_length=30)
    manual_mappings: list[ManualMapping] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def valid_files(self) -> "InputSnapshot":
        ids = [file.id for file in self.files]
        roles = [file.role for file in self.files]
        if (
            len(ids) != len(set(ids))
            or roles.count("old_spec") != 1
            or roles.count("new_spec") != 1
            or not 1 <= roles.count("code") <= 20
            or roles.count("note") > 10
        ):
            raise ValueError("Snapshot requires distinct files within the input contract")
        if len(self.selected_operation_keys) != len(set(self.selected_operation_keys)):
            raise ValueError("Snapshot operations must be unique")
        mapping_ids = [mapping.callsite_id for mapping in self.manual_mappings]
        if len(mapping_ids) != len(set(mapping_ids)):
            raise ValueError("Snapshot manual mappings must be unique")
        return self


class Export(AnalysisModel):
    run: Run
    inputs: InputSnapshot
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    versions: dict[str, Annotated[str, Field(min_length=1, max_length=100)]] = Field(max_length=20)
    evidence: list[Evidence] = Field(max_length=20000)
    reviews: list[Review] = Field(default_factory=list, max_length=600)


class ParserLimits(AnalysisModel):
    max_spec_bytes: int = Field(default=2097152, ge=1, le=2097152)
    max_spec_operations: int = Field(default=100, ge=1, le=100)
    max_code_bytes: int = Field(default=262144, ge=1, le=262144)
    yaml_max_depth: int = Field(default=50, ge=1, le=100)
    yaml_max_nodes: int = Field(default=20000, ge=1, le=100000)
    yaml_max_aliases: int = Field(default=0, ge=0, le=0)
    parser_output_bytes: int = Field(default=4194304, ge=1024, le=4194304)


class Operation(AnalysisModel):
    key: OperationKey
    path: str = Field(max_length=2048)
    method: str = Field(max_length=10)
    json_pointer: str = Field(max_length=4000)
    operation_id: str | None = Field(default=None, max_length=500)


class ParsedSpec(AnalysisModel):
    file_id: UUID
    document: dict[str, object]
    operations: dict[str, Operation] = Field(max_length=100)
    api_base_url: BaseURL | None = None
    warnings: list[str] = Field(default_factory=list, max_length=20)


class ExtractionResult(AnalysisModel):
    file_id: UUID
    language: Language
    callsites: list[CallSite] = Field(default_factory=list, max_length=10000)
    errors: list[str] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=20)


class ParserTask(AnalysisModel):
    kind: Literal["spec", "source"]
    file_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=2097152)
    spec_role: Literal["old_spec", "new_spec"] = "old_spec"
    api_base_url_override: BaseURL | None = None
    limits: ParserLimits = Field(default_factory=ParserLimits)


class ParserResult(AnalysisModel):
    spec: ParsedSpec | None = None
    source: ExtractionResult | None = None
    error: RunError | None = None

    @model_validator(mode="after")
    def exactly_one_result(self) -> "ParserResult":
        if sum(item is not None for item in (self.spec, self.source, self.error)) != 1:
            raise ValueError("Parser result requires exactly one result or error")
        return self

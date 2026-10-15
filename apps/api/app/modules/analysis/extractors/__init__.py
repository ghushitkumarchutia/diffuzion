from pathlib import Path
from uuid import UUID

from app.core.errors import DomainError
from app.modules.analysis.extractors import javascript_typescript, python
from app.modules.analysis.schemas import ExtractionResult, ParserLimits


def extract_source(
    data: bytes, file_id: UUID, filename: str, limits: ParserLimits
) -> ExtractionResult:
    if len(data) > limits.max_code_bytes:
        raise DomainError(413, "input_too_large", "Source exceeds the byte limit")
    try:
        data.decode("utf-8")
    except UnicodeError:
        raise DomainError(422, "invalid_encoding", "Source must be UTF-8") from None
    extension = Path(filename).suffix
    if extension == ".py":
        return python.extract(data, file_id)
    if extension in {".js", ".ts"}:
        return javascript_typescript.extract(
            data, file_id, "javascript" if extension == ".js" else "typescript"
        )
    raise DomainError(422, "unsupported_language", "Only .ts, .js and .py sources are supported")

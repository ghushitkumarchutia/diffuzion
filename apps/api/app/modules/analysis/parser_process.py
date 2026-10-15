import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.subprocesses import run_bounded
from app.modules.analysis.extractors import extract_source
from app.modules.analysis.schemas import ParserLimits, ParserResult, ParserTask, RunError
from app.modules.analysis.specs import parse_spec


def process_task(task: ParserTask) -> ParserResult:
    data = task.content.encode("utf-8")
    try:
        if task.kind == "spec":
            return ParserResult(
                spec=parse_spec(
                    data,
                    task.file_id,
                    task.limits,
                    role=task.spec_role,
                    api_base_url_override=task.api_base_url_override,
                )
            )
        return ParserResult(source=extract_source(data, task.file_id, task.filename, task.limits))
    except DomainError as error:
        return ParserResult(error=RunError(code=error.code, message=error.message))
    except (ValueError, RecursionError, MemoryError):
        return ParserResult(
            error=RunError(code="parser_failed", message="Input could not be parsed safely")
        )


def run_parser(task: ParserTask, settings: Settings) -> ParserResult:
    task = task.model_copy(
        update={
            "limits": ParserLimits.model_validate(
                {key: getattr(settings, key) for key in ParserLimits.model_fields}
            )
        }
    )
    with tempfile.TemporaryDirectory(prefix="diffuzion-parser-") as directory:
        path = Path(directory) / "input.json"
        path.write_text(task.model_dump_json(), encoding="utf-8")
        path.chmod(0o600)
        executable = Path(sys.executable).absolute()
        bootstrap = (
            "import runpy,sys;sys.path.insert(0,sys.argv[1]);"
            "runpy.run_module('app.modules.analysis.parser_process',run_name='__main__')"
        )
        result = run_bounded(
            executable,
            ["-I", "-c", bootstrap, str(Path(__file__).resolve().parents[3]), str(path)],
            settings,
            allowed_executables=frozenset({executable.resolve()}),
        )
        try:
            return ParserResult.model_validate_json(result.stdout)
        except ValidationError:
            raise DomainError(
                503, "parser_invalid_response", "Isolated parser returned invalid output"
            ) from None


def deny_network(event: str, arguments: tuple[Any, ...]) -> None:
    if event.startswith("socket."):
        raise PermissionError("Parser networking is disabled")


def main() -> None:
    sys.addaudithook(deny_network)
    result = None
    limit = 4194304
    try:
        path = Path(sys.argv[-1])
        if not path.is_absolute() or path.stat().st_size > 12 * 1024**2:
            raise ValueError("Input must be a bounded worker-provided local file")
        task = ParserTask.model_validate_json(path.read_bytes())
        limit = task.limits.parser_output_bytes
        result = process_task(task)
    except (OSError, ValueError, ValidationError):
        result = ParserResult(
            error=RunError(code="invalid_parser_input", message="Parser input is invalid")
        )
    output = result.model_dump_json().encode("utf-8")
    if len(output) > limit:
        output = (
            ParserResult(
                error=RunError(
                    code="parser_output_limit", message="Parser output exceeds the limit"
                )
            )
            .model_dump_json()
            .encode()
        )
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()

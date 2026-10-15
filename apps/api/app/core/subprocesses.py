import os
import selectors
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Literal

from app.core.config import Settings
from app.core.errors import DomainError


@dataclass(frozen=True)
class ProcessResult:
    stdout: bytes
    stderr: bytes


class ProcessFailure(DomainError):
    def __init__(self, code: str) -> None:
        super().__init__(503, code, "Isolated processing could not complete")


def run_bounded(
    executable: Path,
    arguments: list[str],
    settings: Settings,
    *,
    allowed_executables: frozenset[Path],
    temp_root: Path | None = None,
    memory_resource: Literal["address_space", "data"] = "address_space",
) -> ProcessResult:
    try:
        resolved = executable.resolve(strict=True)
    except OSError:
        raise ProcessFailure("parser_start_failed") from None
    if not executable.is_absolute() or resolved not in allowed_executables:
        raise ValueError("Executable must be absolute and explicitly trusted")
    command = [str(executable), *arguments]
    if sys.platform == "linux":
        launcher = (
            "import os,resource,sys;"
            "limit=int(sys.argv[1]);"
            "kind=resource.RLIMIT_AS if sys.argv[2]=='address_space' else resource.RLIMIT_DATA;"
            "resource.setrlimit(kind,(limit,limit));"
            "resource.setrlimit(resource.RLIMIT_CORE,(0,0));"
            "os.execv(sys.argv[3],sys.argv[3:])"
        )
        command = [
            sys.executable,
            "-I",
            "-c",
            launcher,
            str(settings.parser_memory_bytes),
            memory_resource,
            *command,
        ]
    with tempfile.TemporaryDirectory(prefix="diffuzion-", dir=temp_root) as directory:
        try:
            process = subprocess.Popen(
                command,
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
                start_new_session=True,
            )
        except OSError:
            raise ProcessFailure("parser_start_failed") from None
        stdout = bytearray()
        stderr = bytearray()
        deadline = monotonic() + settings.parser_timeout_seconds
        try:
            with selectors.DefaultSelector() as selector:
                assert process.stdout is not None and process.stderr is not None
                selector.register(process.stdout, selectors.EVENT_READ, stdout)
                selector.register(process.stderr, selectors.EVENT_READ, stderr)
                while selector.get_map():
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise ProcessFailure("parser_timeout")
                    for key, _ in selector.select(min(remaining, 0.1)):
                        data = os.read(key.fd, 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                        else:
                            key.data.extend(data)
                            if len(stdout) + len(stderr) > settings.parser_output_bytes:
                                raise ProcessFailure("parser_output_limit")
                try:
                    return_code = process.wait(timeout=max(0.001, deadline - monotonic()))
                except subprocess.TimeoutExpired:
                    raise ProcessFailure("parser_timeout") from None
                if return_code:
                    raise ProcessFailure("parser_failed")
                return ProcessResult(bytes(stdout), bytes(stderr))
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                process.kill()
            process.wait()
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

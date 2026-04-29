"""GenericCLIBackend — config-driven CLI subprocess invocation."""
from __future__ import annotations

import logging
import shutil
import subprocess
import time

from agent_nexus.platform.agency.cli_backend.parser import parse_json_output, parse_text_output
from agent_nexus.platform.agency.cli_backend.types import BackendConfig, CLIResult

logger = logging.getLogger(__name__)


class GenericCLIBackend:
    """Generic CLI backend that invokes any CLI via subprocess.

    All CLI-specific behavior is driven by BackendConfig — no per-CLI subclasses needed.
    """

    def __init__(self, config: BackendConfig) -> None:
        self._config = config
        self._available: bool | None = None

    @property
    def name(self) -> str:
        return self._config.command

    @property
    def config(self) -> BackendConfig:
        return self._config

    @property
    def supported_models(self) -> list[str]:
        return list(self._config.model_map.values())

    def resolve_model(self, model_name: str) -> str:
        return self._config.model_map.get(model_name, model_name)

    def is_available(self) -> bool:
        if self._available is None:
            self._available = shutil.which(self._config.command) is not None
        return self._available

    def refresh_availability(self) -> bool:
        self._available = shutil.which(self._config.command) is not None
        return self._available

    def build_args(
        self,
        system_prompt: str,
        user_message: str,
        session_id: str | None = None,
    ) -> list[str]:
        args = list(self._config.args)
        if self._config.system_prompt_flag:
            args.extend([self._config.system_prompt_flag, system_prompt])
        if session_id and self._config.session_flag:
            args.extend([self._config.session_flag, session_id])
        if self._config.output_format == "json" and self._config.output_format_flag:
            args.extend([self._config.output_format_flag, "json"])
        args.append(user_message)
        return args

    def call(
        self,
        system_prompt: str,
        user_message: str,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> CLIResult:
        args = self.build_args(system_prompt, user_message, session_id)
        effective_timeout = timeout or self._config.timeout_secs
        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                [self._config.command, *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        except OSError:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.exception("CLI '%s' failed to start", self._config.command)
            return CLIResult(
                text="", model="",
                raw_stderr="CLI process error",
                returncode=-1, duration_ms=duration_ms,
            )

        try:
            stdout, stderr = proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                "CLI '%s' timed out after %ss, killed process",
                self._config.command, effective_timeout,
            )
            return CLIResult(
                text="", model="",
                raw_stderr=f"CLI timed out after {effective_timeout}s",
                returncode=-1, duration_ms=duration_ms,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        if proc.returncode != 0:
            logger.warning(
                "CLI '%s' exited with code %d", self._config.command, proc.returncode,
            )
            return CLIResult(
                text="", model="",
                raw_stdout=stdout, raw_stderr=stderr,
                returncode=proc.returncode, duration_ms=duration_ms,
            )
        result = self._parse_output(stdout, stderr)
        result.duration_ms = duration_ms
        result.raw_stdout = stdout
        result.raw_stderr = stderr
        return result

    def _parse_output(self, stdout: str, stderr: str) -> CLIResult:
        if self._config.output_format == "json":
            return parse_json_output(stdout, self._config)
        return parse_text_output(stdout, stderr, self._config)

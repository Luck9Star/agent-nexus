"""CLIProvider — invoke agents via subprocess (local_adapter protocol)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from tests.capabilities.contracts.schema import CapabilityContract
from tests.capabilities.providers.base import ProviderResult, build_test_inputs

_REPO_ROOT = Path(__file__).resolve().parents[3]

_subprocess_exec = asyncio.create_subprocess_exec


def _agent_package_dir(contract: CapabilityContract) -> str:
    subdir = "atomic" if contract.agent_type == "atomic" else "composite"
    return str(_REPO_ROOT / "agents" / subdir / contract.agent_name)


class CLIProvider:
    """Invoke agents through local_adapter stdin/stdout JSON-lines protocol."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def invoke_sync(
        self,
        contract: CapabilityContract,
        inputs: dict[str, Any] | None = None,
    ) -> ProviderResult:
        return asyncio.run(self.invoke(contract, inputs))

    async def invoke(
        self,
        contract: CapabilityContract,
        inputs: dict[str, Any] | None = None,
    ) -> ProviderResult:
        if inputs is None:
            inputs = build_test_inputs(contract)

        message = (
            json.dumps(
                {
                    "method": contract.cli_method,
                    "params": inputs,
                }
            )
            + "\n"
        )

        module_name = f"agent_{contract.agent_name.replace('-', '_')}.main"

        start = time.monotonic()
        try:
            env = {**os.environ, "AGENT_MODE": "local"}
            args: list[str] = ["uv", "run"]
            # Composite agents import from agent_nexus which lives in the
            # platform venv.  Use --active so uv targets the already-active
            # environment instead of creating a per-agent venv.
            if contract.agent_type == "composite":
                args.append("--active")
            args.extend(["python", "-m", module_name])
            proc = await _subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=_agent_package_dir(contract),
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(message.encode()),
                timeout=self.timeout,
            )
            duration_ms = (time.monotonic() - start) * 1000

            if proc.returncode != 0:
                return ProviderResult(
                    success=False,
                    raw_output=None,
                    exit_code=proc.returncode,
                    duration_ms=duration_ms,
                    error=stderr_bytes.decode(errors="replace"),
                )

            response = json.loads(stdout_bytes.decode())
            if response.get("status") == "ok":
                return ProviderResult(
                    success=True,
                    raw_output=response.get("result"),
                    exit_code=0,
                    duration_ms=duration_ms,
                )
            return ProviderResult(
                success=False,
                raw_output=response,
                exit_code=0,
                duration_ms=duration_ms,
                error=response.get("error", "Unknown error from local_adapter"),
            )

        except TimeoutError:
            return ProviderResult(
                success=False,
                raw_output=None,
                duration_ms=(time.monotonic() - start) * 1000,
                error=f"Timeout after {self.timeout}s",
            )
        except Exception as exc:
            return ProviderResult(
                success=False,
                raw_output=None,
                duration_ms=(time.monotonic() - start) * 1000,
                error=str(exc),
            )

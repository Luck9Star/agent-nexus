"""Unit tests for AgentSigner -- sign and verify agent packages.

Covers:
- Sign + verify roundtrip (GPG, mocked)
- Tampered agent fails verification (content hash mismatch)
- Missing sigstore falls back to GPG (mock)
- Missing both sigstore and GPG raises RuntimeError
- SignatureBundle and VerificationResult serialization
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.platform.local.signer import (
    AgentSigner,
    SignatureBundle,
    SigningMethod,
    VerificationResult,
    compute_agent_hash,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def agent_dir(tmp_path: Path) -> Path:
    """Create a minimal agent directory with a few files."""
    agent = tmp_path / "my-agent"
    agent.mkdir()
    (agent / "SKILL.md").write_text("# My Agent\n", encoding="utf-8")
    (agent / "agent.toml").write_text(
        '[agent]\nname = "test"\nversion = "1.0.0"\ntype = "atomic"\n',
        encoding="utf-8",
    )
    src = agent / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')\n", encoding="utf-8")
    return agent


@pytest.fixture()
def mock_gpg_sign() -> MagicMock:
    """Mock _gpg_sign to return a deterministic signature."""
    fake_sig = "-----BEGIN PGP SIGNATURE-----\nFAKESIGDATA\n-----END PGP SIGNATURE-----\n"
    with patch(
        "agent_nexus.platform.local.signer._gpg_sign",
        new_callable=AsyncMock,
        return_value=(fake_sig, "ABCD1234"),
    ) as m:
        yield m


@pytest.fixture()
def mock_gpg_verify_ok() -> MagicMock:
    """Mock _gpg_verify to return success."""
    with patch(
        "agent_nexus.platform.local.signer._gpg_verify",
        new_callable=AsyncMock,
        return_value=(True, "ABCD1234"),
    ) as m:
        yield m


@pytest.fixture()
def mock_gpg_verify_fail() -> MagicMock:
    """Mock _gpg_verify to return failure."""
    with patch(
        "agent_nexus.platform.local.signer._gpg_verify",
        new_callable=AsyncMock,
        return_value=(False, "ABCD1234"),
    ) as m:
        yield m


# ---------------------------------------------------------------------------
# Tests: compute_agent_hash
# ---------------------------------------------------------------------------


class TestComputeAgentHash:
    """Tests for the content hashing function."""

    def test_deterministic(self, agent_dir: Path) -> None:
        """Same files produce the same hash."""
        h1 = compute_agent_hash(agent_dir)
        h2 = compute_agent_hash(agent_dir)
        assert h1 == h2

    def test_changes_on_content_edit(self, agent_dir: Path) -> None:
        """Editing a file changes the hash."""
        h_before = compute_agent_hash(agent_dir)
        (agent_dir / "SKILL.md").write_text("# Modified\n", encoding="utf-8")
        h_after = compute_agent_hash(agent_dir)
        assert h_before != h_after

    def test_excludes_git_dir(self, agent_dir: Path) -> None:
        """Files under .git/ are excluded from the hash."""
        h_before = compute_agent_hash(agent_dir)
        git_dir = agent_dir / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        h_after = compute_agent_hash(agent_dir)
        assert h_before == h_after

    def test_empty_dir(self, tmp_path: Path) -> None:
        """Empty directory produces a valid hash (hash of empty string)."""
        empty = tmp_path / "empty"
        empty.mkdir()
        h = compute_agent_hash(empty)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest


# ---------------------------------------------------------------------------
# Tests: sign + verify roundtrip
# ---------------------------------------------------------------------------


class TestSignVerifyRoundtrip:
    """Sign then verify an agent package (GPG, mocked)."""

    @pytest.mark.asyncio
    async def test_gpg_roundtrip(
        self,
        agent_dir: Path,
        mock_gpg_sign: MagicMock,
        mock_gpg_verify_ok: MagicMock,
    ) -> None:
        """Sign with GPG, then verify -- should succeed."""
        # Force GPG method
        with patch(
            "agent_nexus.platform.local.signer._sigstore_is_available",
            return_value=False,
        ):
            signer = AgentSigner()

        bundle = await signer.sign(agent_dir)
        assert bundle.method == SigningMethod.GPG
        assert bundle.signature
        assert bundle.agent_hash == compute_agent_hash(agent_dir)

        result = await signer.verify(agent_dir, bundle)
        assert result.valid is True
        assert result.error_message == ""
        assert result.method == SigningMethod.GPG

    @pytest.mark.asyncio
    async def test_sigstore_roundtrip_when_available(
        self,
        agent_dir: Path,
    ) -> None:
        """When sigstore is available and identity_token provided, use sigstore."""
        fake_sig = "base64encodedsig=="

        with (
            patch(
                "agent_nexus.platform.local.signer._sigstore_is_available",
                return_value=True,
            ),
            patch.object(
                AgentSigner,
                "_sigstore_sign_async",
                new_callable=AsyncMock,
                return_value=(fake_sig, "test@example.com"),
            ),
        ):
            signer = AgentSigner()
            bundle = await signer.sign(agent_dir, identity_token="fake-token")

            assert bundle.method == SigningMethod.SIGSTORE
            assert bundle.signature == fake_sig
            assert bundle.signer_id == "test@example.com"

            # Verify inside the same patch context so _sigstore_is_available
            # still returns True during verification.
            result = await signer.verify(agent_dir, bundle)
            assert result.valid is True
            assert result.method == SigningMethod.SIGSTORE


# ---------------------------------------------------------------------------
# Tests: tamper detection
# ---------------------------------------------------------------------------


class TestTamperDetection:
    """Verify that tampered agents fail verification."""

    @pytest.mark.asyncio
    async def test_tampered_agent_fails(
        self,
        agent_dir: Path,
        mock_gpg_sign: MagicMock,
    ) -> None:
        """Modifying a file after signing causes verification failure."""
        with patch(
            "agent_nexus.platform.local.signer._sigstore_is_available",
            return_value=False,
        ):
            signer = AgentSigner()

        bundle = await signer.sign(agent_dir)

        # Tamper with the agent
        (agent_dir / "SKILL.md").write_text("# TAMPERED!\n", encoding="utf-8")

        result = await signer.verify(agent_dir, bundle)
        assert result.valid is False
        assert "tampered" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_added_file_fails(
        self,
        agent_dir: Path,
        mock_gpg_sign: MagicMock,
    ) -> None:
        """Adding a new file after signing causes verification failure."""
        with patch(
            "agent_nexus.platform.local.signer._sigstore_is_available",
            return_value=False,
        ):
            signer = AgentSigner()

        bundle = await signer.sign(agent_dir)

        # Add a new file
        (agent_dir / "extra.py").write_text("# extra\n", encoding="utf-8")

        result = await signer.verify(agent_dir, bundle)
        assert result.valid is False

    @pytest.mark.asyncio
    async def test_deleted_file_fails(
        self,
        agent_dir: Path,
        mock_gpg_sign: MagicMock,
    ) -> None:
        """Deleting a file after signing causes verification failure."""
        with patch(
            "agent_nexus.platform.local.signer._sigstore_is_available",
            return_value=False,
        ):
            signer = AgentSigner()

        bundle = await signer.sign(agent_dir)

        # Delete a file
        (agent_dir / "src" / "main.py").unlink()

        result = await signer.verify(agent_dir, bundle)
        assert result.valid is False


# ---------------------------------------------------------------------------
# Tests: fallback behavior
# ---------------------------------------------------------------------------


class TestFallbackBehavior:
    """Test that sigstore -> GPG -> RuntimeError fallback works."""

    @pytest.mark.asyncio
    async def test_sigstore_unavailable_falls_back_to_gpg(
        self,
        agent_dir: Path,
        mock_gpg_sign: MagicMock,
    ) -> None:
        """When sigstore is unavailable, GPG is used as fallback."""
        with patch(
            "agent_nexus.platform.local.signer._sigstore_is_available",
            return_value=False,
        ):
            signer = AgentSigner()
            assert signer._method == SigningMethod.GPG

            bundle = await signer.sign(agent_dir)
            assert bundle.method == SigningMethod.GPG

        mock_gpg_sign.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_both_unavailable_raises_runtime_error(
        self,
        agent_dir: Path,
    ) -> None:
        """When neither sigstore nor GPG is available, raise RuntimeError."""
        with (
            patch(
                "agent_nexus.platform.local.signer._sigstore_is_available",
                return_value=False,
            ),
            patch(
                "agent_nexus.platform.local.signer._gpg_sign",
                new_callable=AsyncMock,
                side_effect=FileNotFoundError("gpg not found"),
            ),
        ):
            signer = AgentSigner()

            with pytest.raises(RuntimeError, match="Neither sigstore nor GPG"):
                await signer.sign(agent_dir)

    @pytest.mark.asyncio
    async def test_sigstore_failure_falls_back_to_gpg(
        self,
        agent_dir: Path,
        mock_gpg_sign: MagicMock,
    ) -> None:
        """When sigstore signing fails, fall back to GPG."""
        with (
            patch(
                "agent_nexus.platform.local.signer._sigstore_is_available",
                return_value=True,
            ),
            patch.object(
                AgentSigner,
                "_sigstore_sign_async",
                new_callable=AsyncMock,
                side_effect=RuntimeError("sigstore failed"),
            ),
        ):
            signer = AgentSigner()
            bundle = await signer.sign(agent_dir, identity_token="fake-token")

        # Should have fallen back to GPG
        assert bundle.method == SigningMethod.GPG
        mock_gpg_sign.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case handling."""

    @pytest.mark.asyncio
    async def test_sign_nonexistent_dir_raises(self) -> None:
        """Signing a nonexistent directory raises ValueError."""
        signer = AgentSigner()
        with pytest.raises(ValueError, match="Not a directory"):
            await signer.sign(Path("/nonexistent/path"))

    @pytest.mark.asyncio
    async def test_verify_nonexistent_dir_fails(self) -> None:
        """Verifying against a nonexistent directory returns invalid result."""
        signer = AgentSigner()
        bundle = SignatureBundle(
            signature="sig",
            signer_id="test",
            timestamp=datetime.now(UTC),
            agent_hash="abc123",
            method=SigningMethod.GPG,
        )
        result = await signer.verify(Path("/nonexistent/path"), bundle)
        assert result.valid is False
        assert "Not a directory" in result.error_message

    @pytest.mark.asyncio
    async def test_gpg_verification_failure(
        self,
        agent_dir: Path,
        mock_gpg_sign: MagicMock,
        mock_gpg_verify_fail: MagicMock,
    ) -> None:
        """When GPG verification fails, result is invalid."""
        with patch(
            "agent_nexus.platform.local.signer._sigstore_is_available",
            return_value=False,
        ):
            signer = AgentSigner()

        bundle = await signer.sign(agent_dir)
        result = await signer.verify(agent_dir, bundle)

        assert result.valid is False
        assert "failed" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_sigstore_verify_without_package(
        self,
        agent_dir: Path,
    ) -> None:
        """Verifying a sigstore bundle without sigstore installed reports error."""
        bundle = SignatureBundle(
            signature="sig",
            signer_id="test@sigstore.dev",
            timestamp=datetime.now(UTC),
            agent_hash=compute_agent_hash(agent_dir),
            method=SigningMethod.SIGSTORE,
        )

        with patch(
            "agent_nexus.platform.local.signer._sigstore_is_available",
            return_value=False,
        ):
            signer = AgentSigner()
            result = await signer.verify(agent_dir, bundle)

        assert result.valid is False
        assert "sigstore" in result.error_message.lower()


# ---------------------------------------------------------------------------
# Tests: serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    """SignatureBundle and VerificationResult Pydantic serialization."""

    def test_signature_bundle_json_roundtrip(self) -> None:
        """SignatureBundle serializes to JSON and back."""
        now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
        bundle = SignatureBundle(
            signature="base64sig==",
            signer_id="test-key-id",
            timestamp=now,
            agent_hash="a" * 64,
            method=SigningMethod.GPG,
        )

        json_str = bundle.model_dump_json()
        restored = SignatureBundle.model_validate_json(json_str)

        assert restored.signature == bundle.signature
        assert restored.signer_id == bundle.signer_id
        assert restored.timestamp == bundle.timestamp
        assert restored.agent_hash == bundle.agent_hash
        assert restored.method == bundle.method

    def test_signature_bundle_dict_roundtrip(self) -> None:
        """SignatureBundle serializes to dict and back."""
        now = datetime.now(UTC)
        bundle = SignatureBundle(
            signature="sig",
            signer_id="id",
            timestamp=now,
            agent_hash="b" * 64,
            method=SigningMethod.SIGSTORE,
        )

        d = bundle.model_dump()
        restored = SignatureBundle(**d)

        assert restored.signature == bundle.signature
        assert restored.method == SigningMethod.SIGSTORE

    def test_verification_result_json_roundtrip(self) -> None:
        """VerificationResult serializes to JSON and back."""
        result = VerificationResult(
            valid=True,
            signer_id="test@sigstore.dev",
            method=SigningMethod.SIGSTORE,
            error_message="",
        )

        json_str = result.model_dump_json()
        restored = VerificationResult.model_validate_json(json_str)

        assert restored.valid is True
        assert restored.signer_id == "test@sigstore.dev"
        assert restored.method == SigningMethod.SIGSTORE
        assert restored.error_message == ""

    def test_verification_result_failure(self) -> None:
        """VerificationResult with error serializes correctly."""
        result = VerificationResult(
            valid=False,
            signer_id="key-id",
            method=SigningMethod.GPG,
            error_message="Signature verification failed",
        )

        d = result.model_dump()
        restored = VerificationResult(**d)

        assert restored.valid is False
        assert "failed" in restored.error_message

    def test_signing_method_values(self) -> None:
        """SigningMethod enum has expected values."""
        assert SigningMethod.SIGSTORE == "sigstore"
        assert SigningMethod.GPG == "gpg"

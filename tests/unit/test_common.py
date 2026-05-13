"""Tests for models/_common.py — shared utilities: _utc_now, _MissingSentinel, FrozenModel."""

from __future__ import annotations

import pickle
from datetime import datetime

import pytest
from pydantic import ValidationError

from agent_nexus.models._common import (
    _MISSING,
    FrozenModel,
    _MissingSentinel,
    _utc_now,
)


class TestUtcNow:
    def test_returns_utc_datetime(self):
        result = _utc_now()
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_monotonic(self):
        """Subsequent calls return non-decreasing timestamps."""
        t1 = _utc_now()
        t2 = _utc_now()
        assert t2 >= t1


class TestMissingSentinel:
    def test_singleton_identity(self):
        a = _MissingSentinel()
        b = _MissingSentinel()
        assert a is b

    def test_repr(self):
        assert repr(_MISSING) == "<MISSING>"

    def test_pickle_round_trip(self):
        # Pickle is safe here: _MissingSentinel is an internal sentinel,
        # not user-supplied data. __reduce__ returns our own _restore_missing.
        restored = pickle.loads(pickle.dumps(_MISSING))
        assert restored is _MISSING


class TestFrozenModel:
    def test_frozen_prevents_assignment(self):
        class StrictModel(FrozenModel):
            value: int = 1

        m = StrictModel()
        with pytest.raises(ValidationError):
            m.value = 99

    def test_frozen_is_hashable(self):
        class HashableModel(FrozenModel):
            x: int = 1

        m = HashableModel()
        assert hash(m)  # should not raise

    def test_model_copy_allows_update(self):
        class Pair(FrozenModel):
            a: int = 1
            b: int = 2

        original = Pair()
        modified = original.model_copy(update={"a": 10})
        assert original.a == 1
        assert modified.a == 10

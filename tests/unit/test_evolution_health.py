"""Unit tests for agent_nexus.platform.evolution.health module."""

from unittest.mock import MagicMock

from agent_nexus.models.evolution import EvolutionType, SkillRecord
from agent_nexus.platform.evolution.analyzer import EvolutionSuggestion
from agent_nexus.platform.evolution.health import (
    HealthChecker,
    HealthReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill(
    id: str = "sk-1",
    name: str = "test-skill",
    selections: int = 0,
    applied: int = 0,
    completions: int = 0,
    fallbacks: int = 0,
) -> SkillRecord:
    return SkillRecord(
        id=id,
        name=name,
        total_selections=selections,
        total_applied=applied,
        total_completions=completions,
        total_fallbacks=fallbacks,
    )


def _make_store(skills: list[SkillRecord] | None = None) -> MagicMock:
    store = MagicMock()
    store.get_active_skills.return_value = skills or []
    return store


# ---------------------------------------------------------------------------
# check_health -- three threshold rules
# ---------------------------------------------------------------------------

class TestCheckHealth:
    """Verify the three rules from docs/04 Section 6."""

    def test_zero_selections_returns_empty(self):
        """No data means no suggestion."""
        store = _make_store()
        checker = HealthChecker(store)
        suggestions = checker.check_health(_skill(selections=0))
        assert suggestions == []

    def test_rule1_high_fallback_triggers_fix(self):
        """fallback_rate > 0.4 -> FIX."""
        store = _make_store()
        checker = HealthChecker(store)
        # 10 selections, 6 fallbacks -> fallback_rate = 0.6 > 0.4
        sk = _skill(selections=10, applied=6, completions=6, fallbacks=0)
        # fallbacks need to be within applied, so: sel=10, app=6, comp=0, fb=6
        sk = _skill(id="sk-fb", selections=10, applied=6, completions=0, fallbacks=6)
        suggestions = checker.check_health(sk)
        assert len(suggestions) >= 1
        assert suggestions[0].evolution_type == EvolutionType.FIX
        assert sk.id in suggestions[0].target_skill_ids

    def test_rule2_applied_but_low_completion_triggers_fix(self):
        """applied_rate > 0.4 AND completion_rate < 0.35 -> FIX."""
        store = _make_store()
        checker = HealthChecker(store)
        # sel=10, app=6 (0.6>0.4), comp=1 (1/6=0.17<0.35), fb=5 (1+5=6=app)
        sk = _skill(selections=10, applied=6, completions=1, fallbacks=5)
        suggestions = checker.check_health(sk)
        fix_suggestions = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) >= 1

    def test_rule3_low_effectiveness_triggers_derived(self):
        """effective_rate < 0.55 AND applied_rate > 0.25 -> DERIVED
        (only when FIX is not triggered)."""
        store = _make_store()
        checker = HealthChecker(store)
        # sel=10, app=4 (0.4<=0.4 -> no FIX), comp=3 -> eff=0.3<0.55, app_rate=0.4>0.25
        # Wait, applied_rate=0.4 is NOT > 0.4 (exclusive), so no FIX from rule1
        # But rule2: applied_rate > 0.4 ... 0.4 is not > 0.4 either
        # So FIX is skipped, DERIVED fires.
        # Need: fallback_rate <= 0.4, applied_rate NOT > 0.4 (for FIX rules)
        # But applied_rate > 0.25 for DERIVED
        # sel=10, app=3 (0.3, not >0.4 for fix, but >0.25 for derived)
        # comp=2 (0.67 completion), eff=0.2 < 0.55
        sk = _skill(selections=10, applied=3, completions=2, fallbacks=1)
        suggestions = checker.check_health(sk)
        derived = [s for s in suggestions if s.evolution_type == EvolutionType.DERIVED]
        assert len(derived) == 1
        assert sk.id in derived[0].target_skill_ids

    def test_healthy_skill_returns_empty(self):
        """Skill with good metrics should produce no suggestions."""
        store = _make_store()
        checker = HealthChecker(store)
        # sel=10, app=9 (0.9, high applied), comp=8 (8/9=0.89, high completion)
        # fb=1 -> fallback_rate=0.1 < 0.4
        # applied_rate=0.9 > 0.4, but completion_rate=0.89 >= 0.35 -> no FIX
        # effective_rate=0.8 >= 0.55 -> no DERIVED
        sk = _skill(selections=10, applied=9, completions=8, fallbacks=1)
        suggestions = checker.check_health(sk)
        assert suggestions == []

    def test_fix_dedup_keeps_highest_confidence(self):
        """When both FIX rules fire, only the highest-confidence FIX is kept."""
        store = _make_store()
        checker = HealthChecker(store)
        # sel=10, app=7, comp=1, fb=6
        # fallback_rate=6/10=0.6 -> FIX (conf=0.6)
        # applied_rate=7/10=0.7>0.4, completion_rate=1/7=0.14<0.35 -> FIX (conf=0.7*0.86=0.602)
        sk = _skill(selections=10, applied=7, completions=1, fallbacks=6)
        suggestions = checker.check_health(sk)
        fix_suggestions = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) == 1
        # The FIX from rule2 has higher confidence
        assert fix_suggestions[0].confidence >= 0.6

    def test_rule2_replaces_rule1_when_higher_confidence(self):
        """Rule 2 FIX replaces Rule 1 FIX when its confidence is strictly higher."""
        store = _make_store()
        checker = HealthChecker(store)
        # sel=10, app=8, comp=0, fb=5
        # fallback_rate=5/10=0.5 -> Rule1 FIX (conf=0.5)
        # applied_rate=8/10=0.8>0.4, completion_rate=0/8=0.0<0.35 -> Rule2 FIX (conf=0.8*1.0=0.8)
        # Rule2 has higher confidence (0.8 > 0.5) so it replaces Rule1
        sk = _skill(selections=10, applied=8, completions=0, fallbacks=5)
        suggestions = checker.check_health(sk)
        fix_suggestions = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) == 1
        # Verify it mentions the low completion direction (Rule 2)
        assert "completion" in fix_suggestions[0].direction.lower()


# ---------------------------------------------------------------------------
# HealthReport.summary
# ---------------------------------------------------------------------------

class TestHealthReport:
    def test_summary_healthy(self):
        report = HealthReport(
            skill_id="sk-1",
            skill_name="good-skill",
            is_healthy=True,
            suggestions=[],
            metrics={"applied_rate": 0.9, "total_selections": 100.0},
        )
        text = report.summary()
        assert "[HEALTHY]" in text
        assert "good-skill" in text
        assert "90.00%" in text  # rate formatted as percentage
        assert "100" in text  # total_selections not a rate

    def test_summary_unhealthy_with_suggestions(self):
        report = HealthReport(
            skill_id="sk-2",
            skill_name="bad-skill",
            is_healthy=False,
            suggestions=[
                EvolutionSuggestion(
                    evolution_type=EvolutionType.FIX,
                    target_skill_ids=["sk-2"],
                    direction="High fallback rate",
                    confidence=0.8,
                ),
            ],
            metrics={"fallback_rate": 0.6},
        )
        text = report.summary()
        assert "[UNHEALTHY]" in text
        assert "High fallback rate" in text
        assert "fix" in text.lower()

    def test_summary_with_empty_metrics(self):
        """HealthReport with no metrics should not crash."""
        report = HealthReport(
            skill_id="sk-3",
            skill_name="empty-metrics",
            is_healthy=True,
            suggestions=[],
            metrics={},
        )
        text = report.summary()
        assert "[HEALTHY]" in text
        assert "empty-metrics" in text

    def test_summary_captured_with_no_target_ids(self):
        """Suggestion with empty target_skill_ids shows '(new)'."""
        report = HealthReport(
            skill_id="sk-4",
            skill_name="captured-skill",
            is_healthy=False,
            suggestions=[
                EvolutionSuggestion(
                    evolution_type=EvolutionType.CAPTURED,
                    target_skill_ids=[],
                    direction="New pattern found",
                    confidence=0.5,
                ),
            ],
            metrics={},
        )
        text = report.summary()
        assert "(new)" in text


# ---------------------------------------------------------------------------
# diagnose_all / diagnose_skills
# ---------------------------------------------------------------------------

class TestDiagnoseSkills:
    def test_diagnose_all_returns_reports_for_active_skills(self):
        skills = [
            _skill(id="s1", name="healthy", selections=10, applied=9, completions=8, fallbacks=1),
            _skill(id="s2", name="sick", selections=10, applied=3, completions=2, fallbacks=1),
        ]
        store = _make_store(skills)
        checker = HealthChecker(store)
        reports = checker.diagnose_all()
        assert set(reports.keys()) == {"s1", "s2"}
        assert reports["s1"].is_healthy is True
        assert reports["s2"].is_healthy is False

    def test_diagnose_skills_filters_by_id_set(self):
        skills = [
            _skill(id="s1", name="a"),
            _skill(id="s2", name="b"),
            _skill(id="s3", name="c"),
        ]
        store = _make_store(skills)
        checker = HealthChecker(store)
        reports = checker.diagnose_skills(skill_ids={"s1", "s3"})
        assert set(reports.keys()) == {"s1", "s3"}

    def test_diagnose_skills_with_empty_store(self):
        store = _make_store([])
        checker = HealthChecker(store)
        reports = checker.diagnose_all()
        assert reports == {}

    def test_diagnose_skills_with_empty_skill_ids_set(self):
        """Passing an empty set should match no skills."""
        skills = [
            _skill(id="s1", name="a"),
            _skill(id="s2", name="b"),
        ]
        store = _make_store(skills)
        checker = HealthChecker(store)
        reports = checker.diagnose_skills(skill_ids=set())
        assert reports == {}

    def test_diagnose_skills_with_nonexistent_id(self):
        """IDs not in active skills produce no reports."""
        skills = [_skill(id="s1", name="a")]
        store = _make_store(skills)
        checker = HealthChecker(store)
        reports = checker.diagnose_skills(skill_ids={"nonexistent"})
        assert reports == {}

    def test_diagnose_all_includes_zero_selection_skills(self):
        """Skills with zero selections still get reports (healthy, no metrics)."""
        skills = [_skill(id="s1", name="zero-sel", selections=0)]
        store = _make_store(skills)
        checker = HealthChecker(store)
        reports = checker.diagnose_all()
        assert "s1" in reports
        assert reports["s1"].is_healthy is True
        assert reports["s1"].metrics["applied_rate"] == 0.0


# ---------------------------------------------------------------------------
# get_unhealthy
# ---------------------------------------------------------------------------

class TestGetUnhealthy:
    def test_filters_healthy_out(self):
        skills = [
            _skill(id="ok", name="ok-skill", selections=10, applied=9, completions=8, fallbacks=1),
            _skill(id="bad", name="bad-skill", selections=10, applied=3, completions=2, fallbacks=1),
        ]
        store = _make_store(skills)
        checker = HealthChecker(store)
        unhealthy = checker.get_unhealthy()
        assert "bad" in unhealthy
        assert "ok" not in unhealthy

    def test_all_healthy_returns_empty(self):
        """When all skills are healthy, get_unhealthy returns empty dict."""
        skills = [
            _skill(id="ok1", name="good-1", selections=10, applied=9, completions=8, fallbacks=1),
            _skill(id="ok2", name="good-2", selections=10, applied=9, completions=8, fallbacks=1),
        ]
        store = _make_store(skills)
        checker = HealthChecker(store)
        unhealthy = checker.get_unhealthy()
        assert unhealthy == {}

    def test_empty_store_returns_empty(self):
        store = _make_store([])
        checker = HealthChecker(store)
        assert checker.get_unhealthy() == {}


# ---------------------------------------------------------------------------
# get_health_summary
# ---------------------------------------------------------------------------

class TestGetHealthSummary:
    def test_summary_counts(self):
        skills = [
            _skill(id="ok", name="ok", selections=10, applied=9, completions=8, fallbacks=1),
            _skill(id="bad", name="bad", selections=10, applied=3, completions=2, fallbacks=1),
        ]
        store = _make_store(skills)
        checker = HealthChecker(store)
        summary = checker.get_health_summary()
        assert summary["total_skills"] == 2
        assert summary["healthy"] == 1
        assert summary["unhealthy"] == 1
        assert "bad" in summary["unhealthy_skills"]

    def test_empty_store_summary(self):
        store = _make_store([])
        checker = HealthChecker(store)
        summary = checker.get_health_summary()
        assert summary["total_skills"] == 0
        assert summary["healthy"] == 0
        assert summary["unhealthy"] == 0


# ---------------------------------------------------------------------------
# store property
# ---------------------------------------------------------------------------

class TestStoreProperty:
    def test_store_returns_underlying_store(self):
        store = _make_store()
        checker = HealthChecker(store)
        assert checker.store is store

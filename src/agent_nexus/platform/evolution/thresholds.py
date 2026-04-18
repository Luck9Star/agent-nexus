"""Evolution health threshold constants (from docs/04 Section 6).

Single source of truth for all evolution health thresholds.
Used by health.py, analyzer.py, and evolver.py to avoid duplication.
"""

# Fallback rate > 0.4 triggers FIX
_FALLBACK_THRESHOLD = 0.4

# Applied rate > 0.4 AND completion rate < 0.35 triggers FIX
_HIGH_APPLIED_FOR_FIX = 0.4
_LOW_COMPLETION_THRESHOLD = 0.35

# Effective rate < 0.55 AND applied rate > 0.25 triggers DERIVED
_MODERATE_EFFECTIVE_THRESHOLD = 0.55
_MIN_APPLIED_FOR_DERIVED = 0.25

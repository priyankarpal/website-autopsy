"""Shared fixtures for the audit-seam test layer (Ticket 01)."""
from website_autospy.models import AutopsyResult


def fresh_result() -> AutopsyResult:
    return AutopsyResult(target="https://ex.test", started_at="t", duration_s=0.0)

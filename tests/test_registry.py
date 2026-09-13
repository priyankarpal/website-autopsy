"""Ticket 01: registry + report-mapping invariants (browser-free)."""
from website_autospy.models import DEEP_CHECKS
from website_autospy import report as report_mod


def test_deep_check_ids_unique_and_shaped():
    ids = [c["id"] for c in DEEP_CHECKS]
    assert len(ids) == len(set(ids)) > 0
    for c in DEEP_CHECKS:
        assert c["id"] and c["category"] and c["label"]


def test_every_issue_kind_maps_to_report_section():
    from website_autospy.models import Issue

    known_kinds = {
        "dead_button", "broken_link", "js_error", "slow", "form",
        "seo", "a11y", "perf", "security", "resource", "content",
    }
    for kind in known_kinds:
        assert kind in report_mod.CATEGORIES, kind
        assert kind in report_mod.SECTION_OF or kind in ("slow", "js_error"), kind
    # every Issue kind default used in engine must be known
    assert Issue(kind="seo", title="t", detail="d", page="p").kind in known_kinds


def test_new_depth_checks_registered():
    ids = {c["id"] for c in DEEP_CHECKS}
    for expected in (
        "seo.duplicates",
        "seo.thin-content",
        "seo.structured",
        "perf.runtime",
        "perf.stability",
        "a11y.interaction",
        "form.validation",
        "sec.posture",
        "sec.secrets",
        "link.chains",
    ):
        assert expected in ids, f"missing deep check {expected}"

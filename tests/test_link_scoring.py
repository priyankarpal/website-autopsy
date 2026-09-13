"""Ticket 06: link hygiene + scoring calibration (pure seam)."""
from website_autospy.engine import audit_link_depth
from website_autospy.models import AutopsyResult


def test_tracking_params_flagged():
    r = AutopsyResult(target="https://ex.test", started_at="t", duration_s=0.0)
    audit_link_depth(
        [{"href": "https://ex.test/a?utm_source=x&fbclid=y", "abs": "https://ex.test/a"}],
        "https://ex.test", None, r)
    assert any(i.check == "link.chains" for i in r.issues)


def test_clean_links_pass_silently():
    r = AutopsyResult(target="https://ex.test", started_at="t", duration_s=0.0)
    audit_link_depth(
        [{"href": "/a", "abs": "https://ex.test/a"}],
        "https://ex.test", None, r)
    assert r.issues == []


def test_scoring_stability_nits_vs_high():
    from website_autospy.models import Issue

    def run_with(sev):
        r = AutopsyResult(target="https://ex.test", started_at="t", duration_s=0.0)
        r.pages = []
        r.issues.append(Issue(kind="seo", title="t", detail="d",
                              page="https://ex.test", severity=sev,
                              check="seo.structured"))
        return r.weirdness_score()

    assert run_with("low") < run_with("high")

    empty = AutopsyResult(target="https://ex.test", started_at="t", duration_s=0.0)
    assert empty.weirdness_score() == 0
    assert empty.health_score() == 100

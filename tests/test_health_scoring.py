"""Ticket: health-meter saturation — scoring must discriminate on large crawls."""
from website_autospy.models import AutopsyResult, Issue, PageRecord


def _result_with(severities: list[str], kinds: list[str] | None = None, npages: int = 29) -> AutopsyResult:
    r = AutopsyResult(target="https://ex.test", started_at="t", duration_s=0.0)
    r.pages = [PageRecord(url=f"https://ex.test/{i}", status=200, load_ms=1000) for i in range(npages)]
    kinds = kinds or ["seo"] * len(severities)
    for sev, kind in zip(severities, kinds):
        r.issues.append(Issue(kind=kind, title="t", detail="d",
                              page="https://ex.test", severity=sev, check="x"))
    return r


def test_large_crawl_does_not_flatline_to_zero_health():
    # Screenshot: 1 critical, 9 high, 134 medium, 107 low on 29 pages.
    # Lower bound uses smallest weight (seo=3) — real mixes score higher.
    sevs = ["critical"] + ["high"] * 9 + ["medium"] * 134 + ["low"] * 107
    r = _result_with(sevs, npages=29)
    weird, health = r.weirdness_score(), r.health_score()
    assert weird < 100, f"saturated: weird={weird}"
    assert health > 0, f"flatlined: health={health}"
    # Still severe — must not flip to Good.
    assert weird >= 40, f"too lenient: weird={weird}"
    assert r.verdict()[0] in ("Poor — significant defects", "Critical — urgent action required")


def test_many_lows_do_not_zero_health():
    r = _result_with(["low"] * 67, npages=29)
    assert r.health_score() > 0
    assert r.weirdness_score() < 100


def test_per_page_normalization_more_pages_less_weird():
    sevs = ["medium"] * 20
    few = _result_with(sevs, npages=2)
    many = _result_with(sevs, npages=20)
    assert many.weirdness_score() < few.weirdness_score()


def test_small_nit_case_stays_healthy():
    r = _result_with(["low"] * 5, npages=5)
    assert r.health_score() >= 80
    assert r.weirdness_score() <= 20


def test_monotonic_more_issues_more_weird():
    base = _result_with(["low"] * 5, npages=5)
    more = _result_with(["low"] * 10, npages=5)
    assert more.weirdness_score() > base.weirdness_score()
    assert more.health_score() == 100 - more.weirdness_score()

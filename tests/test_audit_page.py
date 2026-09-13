"""Audit-phase seam: one interface runs every offline per-page audit (browser-free)."""
from website_autospy.engine import audit_page
from website_autospy.models import AutopsyResult


def _bundle(**over) -> dict:
    base = {
        "seo": {"title": "", "titleLen": 0, "desc": "", "descLen": 0,
                "canonical": False, "robots": "", "h1": 0, "h1Texts": [],
                "skipLevel": False, "headings": [], "og": [False, False, False],
                "favicon": False, "charset": False, "lang": "", "viewport": ""},
        "imgs": [], "btns": [], "links": [], "forms": [], "res": [],
        "totKB": 0, "big": [], "mixed": [], "domNodes": 0, "scripts": 0,
        "dupIds": [], "dupCount": 0, "posTab": 0, "nav": {},
    }
    base.update(over)
    return base


def _result() -> AutopsyResult:
    return AutopsyResult(target="https://ex.test", started_at="t", duration_s=0.0)


def test_offline_audits_run_behind_one_interface():
    r = _result()
    audit_page(_bundle(), "https://ex.test/", 800, None, {}, [], [], r)
    by_check = {i.check for i in r.issues}
    assert "seo.crawlable" in by_check  # missing title
    assert "a11y.structure" in by_check  # missing lang
    assert "a11y.viewport" in by_check  # missing viewport


def test_network_failures_flow_through_the_same_seam():
    r = _result()
    audit_page(_bundle(), "https://ex.test/", 800, None, {},
               ["https://ex.test/app.js :: net::ERR_FAILED"], [], r)
    assert any(i.kind == "resource" for i in r.issues)


def test_clean_bundle_stays_quiet_apart_from_known_gaps():
    r = _result()
    bundle = _bundle(seo={**_bundle()["seo"], "title": "A fine title here yes",
                           "titleLen": 20, "desc": "x" * 120, "descLen": 120,
                           "canonical": True, "h1": 1, "favicon": True,
                           "charset": True, "lang": "en", "viewport": "width=device-width",
                           "og": [True, True, True]})
    audit_page(bundle, "https://ex.test/", 800, None, {}, [], [], r)
    assert not [i for i in r.issues if i.check == "seo.crawlable"]

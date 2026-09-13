"""Report cleanliness: standalone HTML, no decorative icons, controls intact (browser-free)."""
import re

from website_autospy.models import AutopsyResult, Issue, PageRecord, DEEP_CHECKS
from website_autospy.report import SECTIONS, write_report


def _sample_result() -> AutopsyResult:
    r = AutopsyResult(target="https://ex.test", started_at="t", duration_s=1.0)
    r.pages = [PageRecord(url="https://ex.test/", status=200, load_ms=800, title="Home")]
    r.issues = [
        Issue(kind="security", title="t1", detail="d1", page="https://ex.test/",
              severity="high", check="sec.headers", recommendation="Fix it."),
        Issue(kind="seo", title="t2", detail="d2", page="https://ex.test/",
              severity="low", check="seo.crawlable"),
    ]
    return r


def test_report_has_all_sections_and_controls(tmp_path):
    p = write_report(_sample_result(), tmp_path)
    t = p.read_text()
    for sec in SECTIONS:
        assert f"id='sec-{sec}'" in t, sec
    assert "setFilter" in t and 'id="q"' in t
    assert "https://ex.test/" in t


def test_report_has_no_decorative_icons_or_external_assets(tmp_path):
    p = write_report(_sample_result(), tmp_path)
    t = p.read_text()
    for ch in ["🛡", "🔍", "♿", "⚡", "🧩", "🔗", "🖱", "📝", "🖼", "🔬"]:
        assert ch not in t, ch
    assert not re.search(r"<link|@import|cdn|fonts\.google", t, re.I)


def test_coverage_ledger_matches_registry(tmp_path):
    p = write_report(_sample_result(), tmp_path)
    t = p.read_text()
    for c in DEEP_CHECKS:
        assert c["id"] in t, c["id"]

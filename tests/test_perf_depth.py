"""Ticket 03: runtime + weight depth (pure audit seam)."""
from website_autospy.engine import audit_perf_depth
from conftest import fresh_result


def test_script_heavy_page_attributes_culprit():
    r = fresh_result()
    audit_perf_depth(
        {"byType": {"script": 1800, "image": 200, "font": 50},
         "thirdPartyKB": 900, "syncHeadScripts": 4,
         "overflowX": False, "noDims": 0},
        "https://ex.test/a", 1500, None, r)
    hits = [i for i in r.issues if i.check == "perf.runtime"]
    assert hits and "script" in hits[0].detail.lower()


def test_lean_page_passes_silently():
    r = fresh_result()
    audit_perf_depth(
        {"byType": {"script": 100, "image": 100},
         "thirdPartyKB": 10, "syncHeadScripts": 0,
         "overflowX": False, "noDims": 0},
        "https://ex.test/a", 800, None, r)
    assert r.issues == []


def test_layout_stability_flags_overflow():
    r = fresh_result()
    audit_perf_depth(
        {"byType": {}, "thirdPartyKB": 0, "syncHeadScripts": 0,
         "overflowX": True, "noDims": 5},
        "https://ex.test/a", 800, None, r)
    assert any(i.check == "perf.stability" for i in r.issues)


def test_empty_evidence_never_crashes():
    r = fresh_result()
    audit_perf_depth({}, "https://ex.test/a", 0, None, r)
    assert r.issues == []

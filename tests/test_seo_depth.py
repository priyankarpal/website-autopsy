"""Ticket 02: SEO content depth (pure audit seam, no browser)."""
from website_autospy.engine import (
    audit_seo_depth,
    audit_site_seo_duplicates,
)

from conftest import fresh_result


def test_structured_data_gap_emits_issue():
    r = fresh_result()
    audit_seo_depth({"jsonLd": 0, "ogComplete": False, "wordCount": 400},
                    "https://ex.test/a", None, r)
    assert any(i.check == "seo.structured" for i in r.issues)


def test_structured_data_present_passes_silently():
    r = fresh_result()
    audit_seo_depth({"jsonLd": 2, "ogComplete": True, "wordCount": 400},
                    "https://ex.test/a", None, r)
    assert not [i for i in r.issues if i.check == "seo.structured"]


def test_thin_content_flags_placeholder():
    r = fresh_result()
    audit_seo_depth({"jsonLd": 1, "ogComplete": True, "wordCount": 30},
                    "https://ex.test/a", None, r)
    assert any(i.check == "seo.thin-content" and "thin" in i.title.lower() for i in r.issues)


def test_empty_evidence_never_crashes():
    r = fresh_result()
    audit_seo_depth({}, "https://ex.test/a", None, r)
    assert r.issues == []


def test_duplicate_titles_collapse_to_one_issue():
    r = fresh_result()
    pages = [
        {"url": "https://ex.test/a", "title": "Same Title"},
        {"url": "https://ex.test/b", "title": "Same Title"},
        {"url": "https://ex.test/c", "title": "Unique"},
    ]
    audit_site_seo_duplicates(pages, r)
    dupes = [i for i in r.issues if i.check == "seo.duplicates"]
    assert len(dupes) == 1
    assert "ex.test/b" in dupes[0].detail

"""Ticket 05: transport + header depth (pure audit seam)."""
from website_autospy.engine import audit_security_posture

from conftest import fresh_result


def test_weak_csp_grades_distinctly():
    r = fresh_result()
    audit_security_posture(
        "https://ex.test",
        {"content-security-policy": "script-src 'unsafe-inline' 'unsafe-eval'"},
        [{"name": "sid", "secure": True, "httpOnly": True, "sameSite": "Lax"}],
        ["https://cdn.ex.test/a.js"], None, r)
    hits = [i for i in r.issues if i.check == "sec.posture"]
    assert hits and "unsafe" in hits[0].detail.lower()


def test_strong_posture_passes_silently():
    r = fresh_result()
    audit_security_posture(
        "https://ex.test",
        {"content-security-policy": "default-src 'self'; frame-ancestors 'self'; object-src 'none'",
         "strict-transport-security": "max-age=31536000; includeSubDomains; preload",
         "x-content-type-options": "nosniff"},
        [{"name": "sid", "secure": True, "httpOnly": True, "sameSite": "Lax"}],
        [], None, r)
    assert r.issues == []


def test_secret_looking_url_flags_for_review():
    r = fresh_result()
    audit_security_posture(
        "https://ex.test", {}, [],
        ["https://cdn.ex.test/a.js?api_key=AKIAIOSFODNN7EXAMPLE"], None, r)
    hits = [i for i in r.issues if i.check == "sec.secrets"]
    assert len(hits) == 1
    assert hits[0].severity in ("low", "medium")


def test_empty_evidence_never_crashes():
    r = fresh_result()
    audit_security_posture("https://ex.test", {}, [], [], None, r)
    assert r.issues == []


def test_samesite_none_without_secure_flagged():
    r = fresh_result()
    audit_security_posture(
        "https://ex.test", {},
        [{"name": "sid", "secure": False, "httpOnly": True, "sameSite": "None"}],
        [], None, r)
    hits = [i for i in r.issues if i.check == "sec.posture"]
    assert len(hits) == 1
    assert "samesite" in hits[0].detail.lower()

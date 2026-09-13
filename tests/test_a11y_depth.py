"""Ticket 04: accessibility interaction depth (pure audit seam)."""
from website_autospy.engine import audit_a11y_interaction
from conftest import fresh_result


def test_keyboard_trap_flags_positive_tabindex():
    r = fresh_result()
    audit_a11y_interaction(
        {"posTab": 3, "hasSkipLink": False, "landmarks": {"main": 0, "nav": 0},
         "emptyHeadings": 0, "formValidationGaps": []},
        "https://ex.test/a", None, r)
    assert any(i.check == "a11y.interaction" for i in r.issues)


def test_good_page_passes_silently():
    r = fresh_result()
    audit_a11y_interaction(
        {"posTab": 0, "hasSkipLink": True, "landmarks": {"main": 1, "nav": 1},
         "emptyHeadings": 0, "formValidationGaps": []},
        "https://ex.test/a", None, r)
    assert r.issues == []


def test_form_validation_gaps_emit_form_issue():
    r = fresh_result()
    audit_a11y_interaction(
        {"posTab": 0, "hasSkipLink": True, "landmarks": {"main": 1, "nav": 1},
         "emptyHeadings": 0, "formValidationGaps": ["email"]},
        "https://ex.test/a", None, r)
    assert any(i.check == "form.validation" for i in r.issues)


def test_empty_evidence_never_crashes():
    r = fresh_result()
    audit_a11y_interaction({}, "https://ex.test/a", None, r)
    assert r.issues == []

"""Sanity checks on the labeled reviews and the calibration set, no API calls."""
import json
from pathlib import Path

from review_replier import reply as rr

HERE = Path(__file__).parent
REVIEWS = json.loads((HERE / "reviews.json").read_text(encoding="utf-8"))
CALIBRATION = json.loads((HERE / "calibration.json").read_text(encoding="utf-8"))


def test_labels_are_consistent():
    profiles = rr.load_profiles()
    for r in REVIEWS:
        assert r["business"] in profiles and 1 <= r["rating"] <= 5
        assert rr.detect_lang(r["text"], profiles[r["business"]].lang) == r["lang"], r["text"]
        assert not r["escalate"] or r["points"] == []  # escalated reviews get no reply to check points in


def test_set_covers_each_business_and_hard_escalations():
    assert {r["business"] for r in REVIEWS} == set(rr.load_profiles())
    escalations = [r for r in REVIEWS if r["escalate"]]
    missed_by_rule = [r for r in escalations if not rr.escalation_reason(r["text"])]
    assert len(escalations) == 7 and len(missed_by_rule) == 5  # these need the model
    assert not [r["text"] for r in REVIEWS if not r["escalate"] and rr.escalation_reason(r["text"])]  # no false alarms


def test_calibration_replies_trip_the_expected_rules():
    profiles = rr.load_profiles()
    for case in CALIBRATION:
        biz = profiles[case["business"]]
        review = rr.Review(rating=case["rating"], text=case["review"])
        good = next(r for r in case["replies"] if r["kind"] == "good")
        assert rr.check(good["text"], review, biz) == []  # the "good" reply passes every guardrail

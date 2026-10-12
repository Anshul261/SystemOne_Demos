import json
from functools import partial

import pytest
from PIL import Image, ImageDraw

import browser_forms
import stamp_check


def result(model, check, value):
    """A check result as `assess` would build it from a model answer with this positive score."""
    spec = stamp_check.CALIBRATION["models"][model]["checks"][check]
    if check == "color":
        others = {"red": 1 - value, "grey": 0.0}
        return stamp_check.assess(model, check, {"type": "choice", "probabilities": {"blue": value, **others}})
    return stamp_check.assess(model, check, {"type": "noul", "noul": value} if spec["question"]["type"] == "noul"
                              else {"type": "choice", "probabilities": {spec["positive"]: value}})


def confident(model, check, passed):
    thr = stamp_check.CALIBRATION["models"][model]["checks"][check]["threshold"]
    lo, hi = thr / 50, 1 - (1 - thr) / 50
    return result(model, check, hi if passed else lo)


def all_checks(model, failing=()):
    return {c: confident(model, c, c not in failing) for c in
            [c["id"] for c in stamp_check.CHECKS] if stamp_check.reliable(model, c)}


def test_every_trusted_check_passing_is_accepted():
    decision = stamp_check.decide(all_checks("3b"))
    assert decision["verdict"] == "accept"
    assert decision["statuses"]["spelling"] == "skipped"  # a blind spot is reported, never blocking


def test_first_failing_rule_decides_the_reason():
    decision = stamp_check.decide(all_checks("3b", failing={"issuer", "date"}))
    assert (decision["verdict"], decision["label"]) == ("reject", "wrong_issuer")


def test_grey_ink_is_reported_as_a_photocopy():
    results = all_checks("3b")
    results["color"] = stamp_check.assess("3b", "color", {"type": "choice", "probabilities": {"blue": 0.01, "red": 0.09, "grey": 0.9}})
    assert stamp_check.decide(results)["label"] == "photocopy"


def test_empty_box_with_a_stamp_elsewhere_is_outside_box():
    results = all_checks("3b", failing={"in_box"})
    results["anywhere"] = confident("3b", "anywhere", True)
    assert stamp_check.decide(results)["label"] == "outside_box"


def test_answers_near_the_threshold_go_to_review_not_reject():
    results = all_checks("3b")
    thr = stamp_check.CALIBRATION["models"]["3b"]["checks"]["genuine"]["threshold"]
    results["genuine"] = result("3b", "genuine", thr * 0.9)
    decision = stamp_check.decide(results)
    assert decision["verdict"] == "review"
    assert decision["statuses"]["genuine"] == "uncertain"


def test_small_model_escalates_checks_it_is_not_trusted_on():
    asked = []

    def ask(model, checks, which):
        asked.append((model, tuple(checks), which))
        return {c: confident(model, c, True) for c in checks}

    decision = stamp_check.decide(stamp_check.route("omni", ask))
    assert asked == [("omni", ("in_box", "status"), "region")]
    assert decision["verdict"] == "review"
    assert decision["statuses"]["color"] == "unverified"


def test_route_asks_the_whole_page_only_when_the_box_is_empty():
    calls = []

    def ask(model, checks, which):
        calls.append(which)
        return {c: confident(model, c, c != "in_box") for c in checks}

    stamp_check.route("3b", ask)
    assert calls == ["region", "page"]


def test_page_is_found_on_a_coloured_desk_and_left_alone_when_it_fills_the_frame():
    page = Image.new("RGB", (620, 877), (250, 249, 244))
    assert stamp_check.find_page(page) is None
    desk = Image.new("RGB", (800, 1100), (92, 70, 52))
    desk.paste(page, (90, 110))
    box = stamp_check.find_page(desk)
    assert box == pytest.approx((90 / 800, 110 / 1100, 710 / 800, 987 / 1100), abs=0.02)
    region = stamp_check.region_in_image(box)
    assert box[0] < region[0] < region[2] <= box[2]


def test_check_streams_events_and_scores_samples(monkeypatch, tmp_path):
    monkeypatch.setattr(browser_forms, "AuditLog", partial(browser_forms.AuditLog, output_dir=str(tmp_path)))

    class StubD1:
        def system_one(self, model, questions, images):
            assert all(isinstance(image, Image.Image) for image in images)
            answers = {}
            for check, question in questions.items():
                spec = stamp_check.CALIBRATION["models"][model]["checks"][check]
                hi = 1 - (1 - spec["threshold"]) / 50
                answers[check] = ({"type": "noul", "noul": hi} if question["type"] == "noul"
                                  else {"type": "choice", "probabilities": {k: (hi if k == spec["positive"] else (1 - hi) / 2)
                                                                            for k in question["criteria"]}})
            return answers, 12.5

    sample = next(s for s in stamp_check.load_samples() if s["label"] == "valid")
    image = Image.open(stamp_check.SAMPLES / sample["file"])
    events = []
    artifact = stamp_check.run_stamp_check(StubD1(), "3b", [(image, {**sample, "source": "sample"})],
                                           on_event=lambda record, img: events.append((record["event"], img is not None)))
    names = [name for name, _ in events]
    assert names[:3] == ["check_started", "document_received", "region_selected"]
    assert ("document_received", True) in events and ("region_selected", True) in events
    verdict = [json.loads(line) for line in artifact.read_text().splitlines() if '"verdict"' in line][-1]
    assert verdict["verdict"] == "accept" and verdict["correct"] is True and verdict["model_ms"] == 12.5


def test_uploads_must_be_real_images_of_usable_size():
    with pytest.raises(ValueError):
        stamp_check.decode_upload("data:text/plain;base64,aGVsbG8=")
    tiny = Image.new("RGB", (100, 100), "white")
    import base64, io
    buf = io.BytesIO(); tiny.save(buf, "PNG")
    with pytest.raises(ValueError, match="too small"):
        stamp_check.decode_upload("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())

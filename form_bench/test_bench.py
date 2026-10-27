"""Offline tests: case building, form reading, scoring and the System One redirect. No model or network calls."""

import random

import pytest
from playwright.sync_api import sync_playwright

import runners
from bench import same, summary
from cases import load_cases, note, us_units
from targets import LOCAL


def test_us_units_convert_and_round():
    values = us_units({"bp_systolic": 141.6, "bp_diastolic": 90.2, "pulse": 77.9, "respiration": 14.0,
                       "weight": 80.0, "height": 175.0, "temperature": 37.0})
    assert values["weight"] == "176.4" and values["height"] == "68.9" and values["temperature"] == "98.6"
    assert values["bp_systolic"] == "142" and "oxygen_saturation" not in values


def test_notes_state_every_expected_value():
    for case in load_cases(20):
        for key in ("first_name", "last_name", "dob", "postal_code", "bp_systolic", "weight"):
            assert case.expected[key] in case.note
        if not case.expected["temperature"]:
            assert "temp " not in case.note  # an unstated value must stay empty
        assert case.expected["temp_method"].lower() in case.note.lower()


def test_cases_are_repeatable():
    assert load_cases(5, seed=3) == load_cases(5, seed=3)
    expected = load_cases(1)[0].expected
    assert note(expected, random.Random(1)) == note(expected, random.Random(1))


@pytest.mark.parametrize("key, expected, actual, right", [
    ("weight", "209.0", "209", True), ("pulse", "87", "88", False), ("city", "Boston", " boston ", True),
    ("allergies", "latex, peanut", "Peanut,latex", True), ("allergies", "", "No known allergies", True),
    ("allergies", "latex", "", False), ("temperature", "", "98.6", False), ("temperature", "", "", True),
])
def test_same(key, expected, actual, right):
    assert same(key, expected, actual) is right


def test_summary_counts_invented_and_flag_accuracy():
    run = {"fields": {"a": {"expected": "", "actual": "x", "correct": False},
                      "b": {"expected": "1", "actual": "1", "correct": True}},
           "flagged_keys": ["a"], "saved": False, "error": None, "wall_ms": 10, "system_one_calls": 2,
           "llm_calls": 1, "llm_input_tokens": 100, "llm_output_tokens": 10, "system_one_tokens": 50}
    totals = summary([run])
    assert totals["invented"] == 1 and totals["field_accuracy"] == 0.5
    assert totals["flagged"] == 1 and totals["flagged_that_were_wrong"] == 1


def test_read_form_finds_every_answer_key_field():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(LOCAL.screens[0].url)
        _, fields = runners.read_form(page)
        browser.close()
    found = {(f["role"], f["name"]) for f in fields}
    assert set(LOCAL.screens[0].answers.values()) <= found
    sex = next(f for f in fields if f["name"] == "Sex")
    assert sex["options"] == ["Unassigned", "Female", "Male", "Unknown"]


def test_system_one_goes_to_local_d1_without_a_key(monkeypatch):
    sent = {}
    monkeypatch.setattr(runners, "SYSTEM_ONE", "d1")
    monkeypatch.setattr(runners, "_post_json", lambda url, key, body: sent.update(url=url, body=body) or {})
    runners.post_json(runners.TYPESAFE, "k", {"model": "jev-latest", "questions": {}})
    assert sent["url"] == f"{runners.D1_URL}/system_one" and sent["body"]["model"] == runners.D1_MODEL


def test_cerebras_requests_drop_jevs_reasoning_field(monkeypatch):
    sent = {}
    monkeypatch.setattr(runners, "_post_json", lambda url, key, body: sent.update(body=body) or {})
    runners.post_json("https://api.cerebras.ai/v1/chat/completions", "k", {"reasoning": {"enabled": False}})
    assert "reasoning" not in sent["body"] and sent["body"]["reasoning_effort"] == runners.LLM_REASONING


def test_meter_counts_each_kind_of_call(monkeypatch):
    monkeypatch.setattr(runners, "_post_json", lambda url, key, body: {
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "input_tokens": 7}})
    with runners.metered() as spent:
        runners.post_json("https://api.cerebras.ai/v1/chat/completions", "k", {})
        runners.post_json(runners.TYPESAFE, "k", {})
    assert (spent.llm_calls, spent.llm_input_tokens, spent.llm_output_tokens) == (1, 5, 2)
    assert (spent.system_one_calls, spent.system_one_tokens) == (1, 7)

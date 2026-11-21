"""CI guard: fixtures must match the locked SDK and the models in use (spec D3, D11)."""

import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from app.agents.providers.record import CONSTRUCTED_SCENARIOS, RECORDABLE_SCENARIOS
from tests.agents.replay import FIXTURES_DIR

SAMPLE_DATASET = Path(__file__).resolve().parents[3] / "datasets" / "sample.json"


def load_all() -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(FIXTURES_DIR.glob("*.json"))}


def anthropic_models_in_use() -> set[str]:
    data = json.loads(SAMPLE_DATASET.read_text())
    return {
        version_["model"]
        for agent in data["agents"]
        for version_ in agent["versions"]
        if version_["provider"] == "anthropic"
    }


def guard_problems(
    fixtures: dict[str, dict[str, Any]], sdk_version: str, models: set[str]
) -> list[str]:
    problems: list[str] = []
    for name, fixture in fixtures.items():
        if fixture["sdk_version"] != sdk_version:
            hint = (
                " (constructed fixture: check it against the SDK types, then update sdk_version)"
                if fixture["source"] == "constructed" and name not in RECORDABLE_SCENARIOS
                else ""
            )
            problems.append(
                f"{name}.json was made with anthropic {fixture['sdk_version']}, "
                f"but {sdk_version} is installed{hint}"
            )
    for model in sorted(models):
        for scenario in RECORDABLE_SCENARIOS:
            fixture = fixtures.get(scenario)
            if fixture is None or fixture["model"] != model:
                problems.append(f"no {scenario} fixture for model {model}")
    return problems


def guard_message(problems: list[str]) -> str:
    return "Anthropic fixtures are stale; run 'make record'.\n" + "\n".join(
        f"- {p}" for p in problems
    )


def test_fixture_guard() -> None:
    problems = guard_problems(load_all(), version("anthropic"), anthropic_models_in_use())
    assert not problems, guard_message(problems)


def test_guard_catches_sdk_drift_and_missing_model() -> None:
    fixtures = load_all()
    installed = version("anthropic")

    problems = guard_problems(fixtures, installed + ".post1", anthropic_models_in_use())
    assert len(problems) == len(fixtures)
    assert "run 'make record'" in guard_message(problems)

    problems = guard_problems(fixtures, installed, {"claude-new-model"})
    assert problems == [f"no {s} fixture for model claude-new-model" for s in RECORDABLE_SCENARIOS]


def test_every_scenario_has_a_fixture() -> None:
    fixtures = load_all()
    assert sorted(fixtures) == sorted(RECORDABLE_SCENARIOS + CONSTRUCTED_SCENARIOS)
    for name, fixture in fixtures.items():
        assert fixture["scenario"] == name
        # `synthetic`: hand-written stand-in for a recordable scenario, until `make record`.
        assert fixture["source"] in ("recorded", "constructed", "synthetic")
        assert set(fixture) == {
            "scenario",
            "source",
            "sdk_version",
            "model",
            "recorded_at",
            "status_code",
            "body",
        }
    for name in CONSTRUCTED_SCENARIOS:
        assert fixtures[name]["source"] == "constructed"


AWAITING_RECORDING = ("thinking_after_tool", "thinking_answer")
NOT_YET_RECORDED = pytest.mark.xfail(
    strict=True,
    reason="author records the thinking fixtures with make record before approval",
)


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(s, marks=NOT_YET_RECORDED) if s in AWAITING_RECORDING else s
        for s in RECORDABLE_SCENARIOS
    ],
)
def test_recordable_fixtures_are_recorded(scenario: str) -> None:
    source = load_all()[scenario]["source"]
    assert source == "recorded", f"run 'make record' (author only): {scenario} is {source}"

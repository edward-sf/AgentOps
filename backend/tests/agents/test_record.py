"""`make record` is tested only without a key or with a replayed client. It never calls the API."""

import json
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from app.agents.providers import record
from app.agents.tools import TOOLS
from app.config import Settings
from tests.agents.replay import (
    blocks,
    expected_thinking_blocks,
    load_fixture,
    replay_responses,
    wire_form,
    without_thinking,
)


def entries(directory: Path) -> list[Path]:
    return list(directory.iterdir())


def json_files(directory: Path) -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text()) for p in directory.glob("*.json")}


async def test_refuses_without_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(anthropic_api_key=None)
    code = await record.run(settings=settings, out_dir=tmp_path)
    assert code == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err
    assert entries(tmp_path) == []


def test_main_refuses_without_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(record, "get_settings", lambda: Settings(anthropic_api_key=None))
    with pytest.raises(SystemExit) as excinfo:
        record.main()
    assert excinfo.value.code == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


FIRST_FOUR = ("tool_use", "final_answer", "direct_answer", "max_tokens")
"""The plain scenarios, recorded first, one request each, in this order."""


def ok(scenario: str) -> tuple[int, dict[str, Any]]:
    return 200, load_fixture(scenario)["body"]


def no_thinking(scenario: str) -> tuple[int, dict[str, Any]]:
    """A fixture's response without its thinking blocks.

    A recorded fixture may itself contain thinking (adaptive thinking runs on every request), so
    attempts that must lack it are built here rather than taken from the fixture.
    """
    return 200, without_thinking(load_fixture(scenario)["body"])


def first_four() -> list[tuple[int, dict[str, Any]]]:
    return [ok(s) for s in FIRST_FOUR]


def after_tool_success() -> list[tuple[int, dict[str, Any]]]:
    """One `thinking_after_tool` attempt that succeeds: a tool call, then a thinking answer."""
    return [ok("tool_use"), ok("thinking_after_tool")]


def after_tool_failure() -> list[tuple[int, dict[str, Any]]]:
    """One `thinking_after_tool` attempt whose answer (call 2) has no thinking block."""
    return [ok("tool_use"), no_thinking("thinking_after_tool")]


def answer_success() -> list[tuple[int, dict[str, Any]]]:
    """A thinking `thinking_answer`, then the replay check's `end_turn`."""
    return [ok("thinking_answer"), no_thinking("final_answer")]


def user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def tool_use_ids(body: dict[str, Any]) -> list[str]:
    return [b["id"] for b in blocks(body, "tool_use")]


async def assert_tool_loop_request(
    call_1: dict[str, Any], call_2: dict[str, Any], call_1_body: dict[str, Any]
) -> None:
    """Call 2 sends call 1's content back verbatim, then the real `get_weather` results."""
    assert call_1["messages"] == [user(record.THINKING_AFTER_TOOL_QUESTION)]
    assert call_2["messages"][0] == user(record.THINKING_AFTER_TOOL_QUESTION)
    assert call_2["messages"][1] == {
        "role": "assistant",
        "content": wire_form(call_1_body["content"]),
    }
    expected_results = [
        {
            "type": "tool_result",
            "tool_use_id": b["id"],
            "content": await TOOLS["get_weather"].func(b["input"]),
            "is_error": False,
        }
        for b in blocks(call_1_body, "tool_use")
    ]
    assert call_2["messages"][2] == {"role": "user", "content": expected_results}
    assert len(call_2["messages"]) == 3


def assert_weather_bot_shape(request: dict[str, Any]) -> None:
    """The request `make eval` sends for weather-bot version 1."""
    spec = TOOLS["get_weather"].spec
    assert request["system"] == record.SYSTEM_PROMPT
    assert request["tools"] == [
        {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
    ]
    assert request["max_tokens"] == 1024
    assert "thinking" not in request  # adaptive thinking: the parameter is left out


async def test_records_every_recordable_scenario(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []
    client = replay_responses(
        *first_four(), *after_tool_success(), *answer_success(), requests=captured
    )

    code = await record.run(client=client, out_dir=tmp_path)

    assert code == 0
    written = json_files(tmp_path)
    assert sorted(written) == sorted(record.RECORDABLE_SCENARIOS)
    for scenario, fixture in written.items():
        assert fixture["scenario"] == scenario
        assert fixture["source"] == "recorded"
        assert fixture["sdk_version"] == version("anthropic")
        assert fixture["model"] == record.MODEL
        assert fixture["status_code"] == 200
        assert fixture["body"] == load_fixture(scenario)["body"]

    assert len(captured) == 8
    tool_use, final_answer, direct_answer, max_tokens = captured[:4]
    assert tool_use["messages"] == [user("Find the weather in Seattle")]
    assert tool_use["tools"][0]["name"] == "get_weather"
    assert tool_use["system"] == record.SYSTEM_PROMPT
    (tool_use_id,) = tool_use_ids(load_fixture("tool_use")["body"])
    sent_tool_use = [b for b in final_answer["messages"][1]["content"] if b["type"] == "tool_use"]
    assert [b["id"] for b in sent_tool_use] == [tool_use_id]
    assert final_answer["messages"][2]["content"][0]["tool_use_id"] == tool_use_id
    assert direct_answer["messages"] == [user("What is the capital of France?")]
    assert max_tokens["max_tokens"] == 5

    call_1, call_2, answer, replay = captured[4:]
    for request in (call_1, call_2, answer, replay):
        assert_weather_bot_shape(request)
    await assert_tool_loop_request(call_1, call_2, load_fixture("tool_use")["body"])
    assert answer["messages"] == [user(record.THINKING_ANSWER_QUESTION)]
    assert replay["messages"] == [
        user(record.THINKING_ANSWER_QUESTION),
        {
            "role": "assistant",
            "content": wire_form(load_fixture("thinking_answer")["body"]["content"]),
        },
        user(record.REPLAY_FOLLOW_UP),
    ]


async def test_thinking_after_tool_retried_until_call_2_thinks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Attempt 1: call 1 answers without a tool. Attempt 2: call 2 has no thinking.
    # Attempt 3: call 1 itself thinks first (its content must still go back verbatim).
    thinking_call_1 = load_fixture("thinking_tool_use")["body"]
    captured: list[dict[str, Any]] = []
    client = replay_responses(
        *first_four(),
        no_thinking("direct_answer"),
        *after_tool_failure(),
        (200, thinking_call_1),
        ok("thinking_after_tool"),
        *answer_success(),
        requests=captured,
    )

    code = await record.run(client=client, out_dir=tmp_path)

    assert code == 0
    assert (
        json_files(tmp_path)["thinking_after_tool"]["body"]
        == (load_fixture("thinking_after_tool")["body"])
    )
    assert len(captured) == 4 + 1 + 2 + 2 + 2
    assert captured[4]["messages"] == [user(record.THINKING_AFTER_TOOL_QUESTION)]
    await assert_tool_loop_request(captured[5], captured[6], load_fixture("tool_use")["body"])
    await assert_tool_loop_request(captured[7], captured[8], thinking_call_1)
    assert capsys.readouterr().err.splitlines() == [
        "thinking_after_tool: attempt 1 of 5: expected stop_reason 'tool_use', got 'end_turn' "
        "(thinking blocks: 0)",
        "thinking_after_tool: attempt 2 of 5: no thinking block (thinking blocks: 0)",
    ]


async def test_thinking_answer_retried_after_no_thinking_and_wrong_stop_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: list[dict[str, Any]] = []
    client = replay_responses(
        *first_four(),
        *after_tool_success(),
        no_thinking("direct_answer"),
        # A tool call where an answer was expected, here with a thinking block. An unexpected
        # stop reason is a failed attempt, not an abort.
        ok("thinking_tool_use"),
        *answer_success(),
        requests=captured,
    )

    code = await record.run(client=client, out_dir=tmp_path)

    assert code == 0
    assert (
        json_files(tmp_path)["thinking_answer"]["body"] == (load_fixture("thinking_answer")["body"])
    )
    assert len(captured) == 4 + 2 + 3 + 1
    assert all(r["messages"] == [user(record.THINKING_ANSWER_QUESTION)] for r in captured[6:9])
    thinking = expected_thinking_blocks(load_fixture("thinking_tool_use")["body"])
    assert thinking == 1
    assert capsys.readouterr().err.splitlines() == [
        "thinking_answer: attempt 1 of 5: no thinking block (thinking blocks: 0)",
        "thinking_answer: attempt 2 of 5: expected stop_reason 'end_turn', got 'tool_use' "
        f"(thinking blocks: {thinking})",
    ]


async def test_worst_case_request_count(tmp_path: Path) -> None:
    """Both thinking scenarios succeed only on their last attempt: the most `make record` sends."""
    attempts = record.THINKING_ATTEMPTS
    captured: list[dict[str, Any]] = []
    client = replay_responses(
        *first_four(),
        *[r for _ in range(attempts - 1) for r in after_tool_failure()],
        *after_tool_success(),
        *[no_thinking("thinking_answer")] * (attempts - 1),
        *answer_success(),
        requests=captured,
    )

    code = await record.run(client=client, out_dir=tmp_path)

    assert code == 0
    assert len(captured) == record.MAX_REQUESTS == 20


@pytest.mark.parametrize(
    ("scenario", "responses", "requests"),
    [
        (
            "thinking_after_tool",
            lambda: [r for _ in range(5) for r in after_tool_failure()],
            4 + 10,
        ),
        (
            "thinking_answer",
            lambda: [*after_tool_success(), *[no_thinking("thinking_answer")] * 5],
            4 + 2 + 5,
        ),
    ],
)
async def test_five_failed_attempts_write_nothing(
    scenario: str,
    responses: Callable[[], list[tuple[int, dict[str, Any]]]],
    requests: int,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: list[dict[str, Any]] = []
    client = replay_responses(*first_four(), *responses(), requests=captured)

    code = await record.run(client=client, out_dir=tmp_path)

    assert code == 1
    assert len(captured) == requests
    assert capsys.readouterr().err.splitlines() == [
        *(
            f"{scenario}: attempt {n} of 5: no thinking block (thinking blocks: 0)"
            for n in range(1, 6)
        ),
        f"{scenario}: no usable response in 5 attempts; "
        "nothing was written; it is safe to rerun 'make record'",
    ]
    assert entries(tmp_path) == []


async def test_replay_check_failure_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = replay_responses(
        *first_four(), *after_tool_success(), ok("thinking_answer"), ok("tool_use")
    )

    code = await record.run(client=client, out_dir=tmp_path)

    assert code == 1
    assert "thinking replay: expected stop_reason 'end_turn'" in capsys.readouterr().err
    assert entries(tmp_path) == []


async def test_unexpected_stop_reason_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The model answered directly where a tool call was expected.
    direct = load_fixture("direct_answer")["body"]
    client = replay_responses((200, direct))

    code = await record.run(client=client, out_dir=tmp_path)

    assert code == 1
    assert "tool_use" in capsys.readouterr().err
    assert entries(tmp_path) == []


async def test_invalid_key_prints_one_line_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The SDK raises `AuthenticationError` for this 401.
    client = replay_responses(
        (
            401,
            {
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
            },
        )
    )

    code = await record.run(client=client, out_dir=tmp_path)

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("record: anthropic API error: authentication_error (status 401)")
    assert "invalid x-api-key" in lines[0]
    assert entries(tmp_path) == []

"""`make record` is tested only without a key or with a replayed client. It never calls the API."""

import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from app.agents.providers import record
from app.config import Settings
from tests.agents.replay import load_fixture, replay_client, replay_responses


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


async def test_records_every_recordable_scenario(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []
    client = replay_client(
        "tool_use", "final_answer", "direct_answer", "max_tokens", requests=captured
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

    tool_use, final_answer, direct_answer, max_tokens = captured
    assert tool_use["messages"] == [{"role": "user", "content": "Find the weather in Seattle"}]
    assert tool_use["tools"][0]["name"] == "get_weather"
    assert tool_use["system"] == record.SYSTEM_PROMPT
    tool_use_block = next(
        b for b in load_fixture("tool_use")["body"]["content"] if b["type"] == "tool_use"
    )
    assert final_answer["messages"][1]["content"][-1]["id"] == tool_use_block["id"]
    assert final_answer["messages"][2]["content"][0]["tool_use_id"] == tool_use_block["id"]
    assert direct_answer["messages"] == [
        {"role": "user", "content": "What is the capital of France?"}
    ]
    assert max_tokens["max_tokens"] == 5


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

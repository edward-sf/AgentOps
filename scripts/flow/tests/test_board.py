from typing import Any

import pytest

from flow import board, gh

OPTIONS = [{"id": "1", "name": "Ready"}, {"id": "2", "name": "In Progress"}]


def test_find_option_is_case_insensitive() -> None:
    opt = board.find_option(OPTIONS, "In progress")
    assert opt is not None and opt["id"] == "2"


def test_find_option_missing_returns_none() -> None:
    assert board.find_option(OPTIONS, "Merged to milestone") is None


def test_set_status_warns_and_does_not_raise_on_missing_option(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_json(args: list[str], **_: Any) -> Any:
        if "item-add" in args:
            return {"id": "ITEM"}
        if "view" in args:
            return {"id": "PROJ"}
        return {"fields": [{"name": "Status", "id": "F", "options": OPTIONS}]}

    def no_edit(args: list[str], **_: Any) -> str:
        raise AssertionError("must not edit")

    monkeypatch.setattr(gh, "json", fake_json)
    monkeypatch.setattr(gh, "run", no_edit)
    board.set_status(2, "Merged to milestone")
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "Merged to milestone" in err


def test_set_status_swallows_gh_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(args: list[str], **_: Any) -> Any:
        raise gh.GhError("nope")

    monkeypatch.setattr(gh, "json", boom)
    board.set_status(2, "Done")
    assert "warning" in capsys.readouterr().err


def test_set_status_edits_with_matched_option(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_json(args: list[str], **_: Any) -> Any:
        if "item-add" in args:
            return {"id": "ITEM"}
        if "view" in args:
            return {"id": "PROJ"}
        return {"fields": [{"name": "Status", "id": "F", "options": OPTIONS}]}

    monkeypatch.setattr(gh, "json", fake_json)

    def fake_run(args: list[str], **_: Any) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(gh, "run", fake_run)
    board.set_status(2, "In progress")
    assert calls and calls[0][-1] == "2" and "ITEM" in calls[0]

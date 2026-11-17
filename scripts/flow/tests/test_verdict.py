from flow.verdict import Verdict, format_verdict, latest_verdict_for, parse_verdict

SHA = "a" * 40
OLD = "b" * 40


def test_format_then_parse_roundtrip() -> None:
    body = format_verdict("PASS", SHA, "- looks good")
    assert body.splitlines()[0] == f"Review verdict: PASS @ {SHA}"
    assert parse_verdict(body) == Verdict("PASS", SHA)


def test_parse_rejects_short_sha_and_other_comments() -> None:
    assert parse_verdict("Review verdict: PASS @ abc123") is None
    assert parse_verdict("LGTM") is None
    assert parse_verdict("") is None


def test_parse_only_matches_first_line() -> None:
    assert parse_verdict(f"intro\nReview verdict: PASS @ {SHA}") is None
    assert parse_verdict(f"Review verdict: PASS @ {SHA} extra") is None
    assert parse_verdict(f"Review verdict: MAYBE @ {SHA}") is None


def test_latest_verdict_for_head_wins() -> None:
    comments = [format_verdict("PASS", SHA, ""), format_verdict("FAIL", SHA, "")]
    assert latest_verdict_for(comments, SHA) == Verdict("FAIL", SHA)


def test_latest_verdict_ignores_other_shas_and_noise() -> None:
    comments = [format_verdict("PASS", SHA, ""), "chatter", format_verdict("FAIL", OLD, "")]
    assert latest_verdict_for(comments, SHA) == Verdict("PASS", SHA)
    assert latest_verdict_for([], SHA) is None

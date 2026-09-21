"""CLI tests: exit codes are the contract a shell (and CI) sees."""

from nlsql import cli
from nlsql.llm import ScriptableFakeLLMClient


def test_ask_with_unfixable_sql_exits_1_without_a_second_error(monkeypatch, capsys):
    # Every attempt produces SQL the guardrails reject. The CLI must
    # print the attempt report once and exit 1 -- an earlier version
    # re-raised, so main() printed a second, generic "error:" line.
    bad_client = ScriptableFakeLLMClient({"anything": ["DELETE FROM trips"]})
    monkeypatch.setattr(cli, "_client", lambda settings, live: bad_client)
    exit_code = cli.main(["ask", "anything"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "attempt failed" in captured.err
    assert "error:" not in captured.err


def test_info_command_exits_0(capsys):
    assert cli.main(["info"]) == 0
    out = capsys.readouterr().out
    assert "trips: 48,601 rows" in out

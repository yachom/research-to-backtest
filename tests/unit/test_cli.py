"""CLI 골격(README §26, 명세 CLI-integration §7) 스모크 테스트."""

from typer.testing import CliRunner

from research_backtest import __version__
from research_backtest.app.cli import NOT_IMPLEMENTED_EXIT_CODE, app

runner = CliRunner()

ALL_COMMANDS = [
    "resolve-company",
    "collect-financials",
    "collect-market",
    "build-financials",
    "parse-xbrl",
    "reconcile-financials",
    "research",
    "backtest",
    # HITL 워크플로 (1804 §14 + 명세 CLI-integration §5)
    "create-run",
    "runs",
    "status",
    "create-analyst-view",
    "create-hypothesis",
    "approve-strategy",
    "submit-interpretation",
    "generate-candidates",
    "generate-strategy-draft",
    "generate-report",
]


def test_help_lists_all_spec_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ALL_COMMANDS:
        assert cmd in result.output


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_stub_command_exits_with_not_implemented() -> None:
    """research는 아직 스텁(C1')이라 NOT_IMPLEMENTED로 종료한다 (명세 §0 비범위)."""
    result = runner.invoke(
        app,
        ["research", "--company", "SK하이닉스", "--as-of-date", "2025-12-31"],
    )
    assert result.exit_code == NOT_IMPLEMENTED_EXIT_CODE

import json
import os
from pathlib import Path

import pytest

from evals.report import render_report, write_report


# ---------------------------------------------------------------------------
# Helpers to build synthetic cell data
# ---------------------------------------------------------------------------

def _make_meta(
    framework: str,
    case_id: str,
    *,
    status: str = "ok",
    error_reason: str | None = None,
    harness_latency_ms: int = 1000,
    venv_mutated: bool = False,
    sources: dict | None = None,
) -> dict:
    if sources is None:
        sources = {
            "model": "campaign",
            "timeout_s": "campaign",
            "max_steps": "harness-default",
        }
    return {
        "framework": framework,
        "case_id": case_id,
        "status": status,
        "error_reason": error_reason,
        "harness_latency_ms": harness_latency_ms,
        "venv_mutated": venv_mutated,
        "effective_config": {
            "model": "test-model",
            "timeout_s": 120,
            "max_steps": 50,
            "sources": sources,
        },
    }


def _make_scoring(
    *,
    visible_test_outcome: str = "pass",
    hidden_test_outcome: str = "pass",
    changed_files: int = 1,
    added: int = 3,
    removed: int = 1,
    token_input: int | None = 100,
    token_output: int | None = 50,
) -> dict:
    scoring: dict = {
        "visible_test_outcome": visible_test_outcome,
        "hidden_test_outcome": hidden_test_outcome,
        "minimality": {
            "changed_files": changed_files,
            "changed_lines_added": added,
            "changed_lines_removed": removed,
        },
        "edit_constraint_compliance": {
            "disallowed_violations": [],
            "allowed_violations": [],
            "over_max_changed_files": False,
        },
    }
    if token_input is not None and token_output is not None:
        scoring["token_usage"] = {"input": token_input, "output": token_output}
    return scoring


def _write_cell(
    campaign_dir: Path,
    fw: str,
    case: str,
    meta: dict,
    scoring: dict,
) -> None:
    cell = campaign_dir / fw / case
    cell.mkdir(parents=True, exist_ok=True)
    (cell / "meta.json").write_text(json.dumps(meta))
    (cell / "scoring.json").write_text(json.dumps(scoring))


# ---------------------------------------------------------------------------
# Fixture: synthetic on-disk campaign with four cells
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_campaign(tmp_path: Path) -> Path:
    campaign_dir = tmp_path / "runs" / "2026-04-29T00-00-00"
    campaign_dir.mkdir(parents=True)

    manifest = {
        "started_at": "2026-04-29T00:00:00Z",
        "frameworks": ["fw1", "fw2"],
        "cases": ["case1", "case2"],
        "config_overrides": {
            "model": "test-model",
            "timeout_s": 120,
            "max_steps": 50,
        },
    }
    (campaign_dir / "manifest.json").write_text(json.dumps(manifest))

    # fw1/case1: ok + visible=pass + hidden=pass
    _write_cell(
        campaign_dir, "fw1", "case1",
        meta=_make_meta("fw1", "case1", harness_latency_ms=1200),
        scoring=_make_scoring(),
    )

    # fw1/case2: ok + visible=fail + hidden=n/a (no hidden test)
    _write_cell(
        campaign_dir, "fw1", "case2",
        meta=_make_meta("fw1", "case2", harness_latency_ms=2300),
        scoring=_make_scoring(
            visible_test_outcome="fail",
            hidden_test_outcome="n/a",
            token_input=None,
            token_output=None,
        ),
    )

    # fw2/case1: error + timeout, timeout_s sourced from cell-flag
    _write_cell(
        campaign_dir, "fw2", "case1",
        meta=_make_meta(
            "fw2", "case1",
            status="error",
            error_reason="timeout",
            harness_latency_ms=30000,
            sources={
                "model": "campaign",
                "timeout_s": "cell-flag",
                "max_steps": "harness-default",
            },
        ),
        scoring=_make_scoring(visible_test_outcome="error", hidden_test_outcome="error"),
    )

    # fw2/case2: error + nonzero_exit + venv_mutated
    _write_cell(
        campaign_dir, "fw2", "case2",
        meta=_make_meta(
            "fw2", "case2",
            status="error",
            error_reason="nonzero_exit",
            venv_mutated=True,
        ),
        scoring=_make_scoring(visible_test_outcome="fail", hidden_test_outcome="fail"),
    )

    return campaign_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_render_report_contains_header_and_tables(synthetic_campaign: Path) -> None:
    report = render_report(synthetic_campaign)
    assert "# Campaign" in report
    assert "framework | case |" in report
    assert "## Per-framework summary" in report


def test_render_report_marks_cell_level_overrides_with_asterisk(synthetic_campaign: Path) -> None:
    report = render_report(synthetic_campaign)
    # fw2/case1 has timeout_s sourced from cell-flag — row must have fw2* in the framework column
    assert "fw2*" in report


def test_render_report_lists_setup_failures(synthetic_campaign: Path) -> None:
    repo_root = synthetic_campaign.parent.parent
    setup_dir = repo_root / ".runs-cache" / "setup"
    setup_dir.mkdir(parents=True)
    (setup_dir / "x.fail").write_text('{"reason": "nonzero_exit"}')

    report = render_report(synthetic_campaign)
    assert ".runs-cache/setup/x.stderr.log" in report


def test_render_report_lists_venv_mutations(synthetic_campaign: Path) -> None:
    report = render_report(synthetic_campaign)
    # fw2/case2 has venv_mutated=true; Notes should warn about it
    assert "venv" in report.lower()
    assert "fw2/case2" in report


def test_render_report_status_error_includes_reason(synthetic_campaign: Path) -> None:
    report = render_report(synthetic_campaign)
    # fw2/case1 has error_reason="timeout"
    assert "error: timeout" in report


def test_write_report_is_idempotent(synthetic_campaign: Path) -> None:
    write_report(synthetic_campaign)
    report_path = synthetic_campaign / "report.md"
    content1 = report_path.read_bytes()

    write_report(synthetic_campaign)
    content2 = report_path.read_bytes()

    assert content1 == content2

"""CLI-level regression tests focused on misconfiguration surfacing."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from evals import cli
from evals.discovery import discover_cases, discover_frameworks


def _init_repo(repo: Path) -> None:
    (repo / "frameworks").mkdir(parents=True)
    (repo / "cases").mkdir()


def _write_good_framework(repo: Path, name: str = "good") -> None:
    fw = repo / "frameworks" / name
    fw.mkdir()
    (fw / "manifest.json").write_text(
        json.dumps({"entry": "./run.py", "env": [], "model": "fake"})
    )


def _write_malformed_framework(repo: Path, name: str = "broken") -> None:
    fw = repo / "frameworks" / name
    fw.mkdir()
    (fw / "manifest.json").write_text("{ this is not valid json")


def _write_good_case(repo: Path, fixture_dir: Path, case_id: str = "case-001") -> None:
    (repo / "cases" / f"{case_id}.json").write_text(
        json.dumps(
            {
                "case_id": case_id,
                "fixture_repo": str(fixture_dir),
                "failing_test_command": "true",
                "failure_output": "boom",
            }
        )
    )


def test_cmd_eval_new_includes_malformed_framework_in_matrix(
    tmp_path: Path, monkeypatch
) -> None:
    """Malformed framework manifests must appear in the campaign matrix so
    they render as `framework_misconfigured` cells, not silently disappear."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_good_framework(repo, name="good")
    _write_malformed_framework(repo, name="broken")
    fixture = tmp_path / "fix"
    fixture.mkdir()
    _write_good_case(repo, fixture)

    monkeypatch.setattr(cli, "_repo_root", lambda: repo)
    args = cli._build_parser().parse_args(["eval-new"])
    rc = cli.cmd_eval_new(args)
    assert rc == 0

    current = repo / "runs" / "CURRENT"
    manifest = json.loads((current / "manifest.json").read_text())
    assert "good" in manifest["frameworks"]
    assert "broken" in manifest["frameworks"], (
        "malformed framework was silently dropped from the campaign matrix"
    )


# ---------------------------------------------------------------------------
# _prepare_needed — must detect stale fixture/lock hashes, not just missing dirs.
# ---------------------------------------------------------------------------

def _make_cached_repo(tmp_path: Path, *, case_id: str = "case-001") -> tuple[Path, Path]:
    """Set up a repo + populated cache where prepare-needed should be False."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    # Initialize a real git repo so compute_fixture_hash can ls-files.
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=str(repo), check=True, capture_output=True,
    )

    # A fixture committed to git so compute_fixture_hash sees tracked files.
    fixture = repo / "fixtures" / case_id
    fixture.mkdir(parents=True)
    (fixture / "main.py").write_text("x = 1\n")
    (fixture / "pyproject.toml").write_text(
        '[project]\nname="f"\nversion="0"\n'
    )
    _write_good_case(repo, fixture, case_id=case_id)
    _write_good_framework(repo, name="good")

    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo), check=True, capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": __import__("os").environ["PATH"],
        },
    )

    cache = repo / ".runs-cache"
    cache.mkdir()

    # Populate cache with everything _prepare_needed inspects.
    (cache / f"{case_id}.git").mkdir()
    (cache / f"{case_id}.venv").mkdir()

    from evals.workspace import compute_fixture_hash, compute_lock_hash
    (cache / f"{case_id}.fixture-hash").write_text(
        compute_fixture_hash(repo, case_id, fixture)
    )
    (cache / f"{case_id}.lock-hash").write_text(compute_lock_hash(fixture))

    setup_dir = cache / "setup"
    setup_dir.mkdir()
    fw_manifest = repo / "frameworks" / "good" / "manifest.json"
    (setup_dir / "good.ok").write_text(
        json.dumps({"hash": hashlib.sha256(fw_manifest.read_bytes()).hexdigest()})
    )

    return repo, cache


def test_prepare_needed_false_when_caches_are_fresh(tmp_path: Path) -> None:
    repo, cache = _make_cached_repo(tmp_path)
    frameworks, _ = discover_frameworks(repo)
    cases, _ = discover_cases(repo)

    assert cli._prepare_needed(repo, frameworks, cases, cache) is False


def test_prepare_needed_true_when_fixture_hash_is_stale(tmp_path: Path) -> None:
    """Editing a tracked fixture file must trigger a layer rebuild on the next
    `eval-all` even though the bare repo and venv directories still exist.
    """
    repo, cache = _make_cached_repo(tmp_path)
    frameworks, _ = discover_frameworks(repo)
    cases, _ = discover_cases(repo)

    # Mutate a tracked fixture file — fixture_hash now diverges from the
    # value persisted in cache/.fixture-hash.
    case_id = cases[0].case_id
    (repo / "fixtures" / case_id / "main.py").write_text("x = 999\n")

    assert cli._prepare_needed(repo, frameworks, cases, cache) is True, (
        "fixture mutation must trigger prepare; otherwise eval-all reuses a stale bare repo"
    )


def test_prepare_needed_true_when_lock_hash_is_stale(tmp_path: Path) -> None:
    """Editing the case's pyproject.toml/uv.lock must trigger a venv rebuild."""
    repo, cache = _make_cached_repo(tmp_path)
    frameworks, _ = discover_frameworks(repo)
    cases, _ = discover_cases(repo)

    case_id = cases[0].case_id
    (repo / "fixtures" / case_id / "pyproject.toml").write_text(
        '[project]\nname="f"\nversion="1"\n'
    )

    assert cli._prepare_needed(repo, frameworks, cases, cache) is True, (
        "lock-file mutation must trigger prepare; otherwise eval-all reuses a stale venv"
    )


# ---------------------------------------------------------------------------
# eval-all --framework / --case typo handling
# ---------------------------------------------------------------------------

def test_eval_all_unknown_framework_exits_2(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo, _cache = _make_cached_repo(tmp_path)
    monkeypatch.setattr(cli, "_repo_root", lambda: repo)

    args = cli._build_parser().parse_args(["eval-all", "--framework", "typo"])
    rc = cli.cmd_eval_all(args)

    assert rc == 2, "unknown --framework must exit 2, not silently no-op"
    err = capsys.readouterr().err
    assert "typo" in err
    assert "framework" in err.lower()


def test_eval_all_unknown_case_exits_2(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo, _cache = _make_cached_repo(tmp_path)
    monkeypatch.setattr(cli, "_repo_root", lambda: repo)

    args = cli._build_parser().parse_args(["eval-all", "--case", "nope"])
    rc = cli.cmd_eval_all(args)

    assert rc == 2, "unknown --case must exit 2, not silently no-op"
    err = capsys.readouterr().err
    assert "nope" in err
    assert "case" in err.lower()

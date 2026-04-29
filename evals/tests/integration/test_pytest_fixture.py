import json
import os
from pathlib import Path

import pytest

from evals.env import build_test_env
from evals.pipeline import run_test_command
from evals.workspace import ensure_case_venv


@pytest.mark.integration
def test_pytest_fixture_manifest_commands_execute_without_project_install(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """Exercise the real manifest commands in the no-install-project test env.

    The pytest fixture is src-layout and self-hosting. Its shared venv lacks a
    `pytest` console script, so the manifest commands must invoke pytest as a
    Python module from the checked-out source exposed on PYTHONPATH.
    """
    case_id = "pytest-dev__pytest-7571"
    fixture_dir = repo_root / "fixtures" / case_id
    manifest = json.loads((repo_root / "cases" / f"{case_id}.json").read_text())
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    venv = ensure_case_venv(
        repo_root=repo_root,
        case_id=case_id,
        fixture_dir=fixture_dir,
        cache_dir=cache_dir,
    )
    assert not (venv / "bin" / "pytest").exists()

    env = build_test_env(
        case_venv_path=venv,
        cell_repo_path=fixture_dir,
        base_env={k: os.environ[k] for k in ("HOME", "PATH") if k in os.environ},
    )

    visible = run_test_command(
        manifest["failing_test_command"],
        cwd=fixture_dir,
        env=env,
        timeout_s=60,
        output_path=tmp_path / "visible.json",
    )
    hidden = run_test_command(
        manifest["hidden_test_command"],
        cwd=fixture_dir,
        env=env,
        timeout_s=60,
        output_path=tmp_path / "hidden.json",
    )

    visible_payload = json.loads((tmp_path / "visible.json").read_text())
    hidden_payload = json.loads((tmp_path / "hidden.json").read_text())
    combined_output = "\n".join(
        [
            visible_payload["stdout"],
            visible_payload["stderr"],
            hidden_payload["stdout"],
            hidden_payload["stderr"],
        ]
    )
    assert visible.outcome == "fail"
    assert hidden.outcome == "pass"
    assert "Failed to spawn" not in combined_output
    assert not (venv / "bin" / "pytest").exists()
    site_packages = next(venv.glob("lib/python*/site-packages"))
    assert not list(site_packages.glob("pytest_fixture-*.dist-info"))

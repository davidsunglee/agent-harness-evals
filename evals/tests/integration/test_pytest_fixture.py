import json
import os
from pathlib import Path

import pytest

from evals.env import build_test_env
from evals.pipeline import run_test_command
from evals.workspace import ensure_case_venv


@pytest.mark.integration
def test_pytest_fixture_declared_python_module_command_executes_source_without_project_install(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """The real pytest fixture is src-layout and self-hosting.

    Its no-install-project venv intentionally lacks a `pytest` console script;
    cases that need the checked-out `src/pytest` package must declare that via
    `python -m pytest` rather than relying on harness command rewriting.
    """
    fixture_dir = repo_root / "fixtures" / "pytest-dev__pytest-7571"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    venv = ensure_case_venv(
        repo_root=repo_root,
        case_id="pytest-dev__pytest-7571",
        fixture_dir=fixture_dir,
        cache_dir=cache_dir,
    )
    assert not (venv / "bin" / "pytest").exists()

    env = build_test_env(
        case_venv_path=venv,
        cell_repo_path=fixture_dir,
        base_env={k: os.environ[k] for k in ("HOME", "PATH") if k in os.environ},
    )
    output_path = tmp_path / "pytest-version.json"

    result = run_test_command(
        "python -m pytest --version",
        cwd=fixture_dir,
        env=env,
        timeout_s=30,
        output_path=output_path,
    )

    payload = json.loads(output_path.read_text())
    assert result.outcome == "pass"
    assert "pytest" in (payload["stdout"] + payload["stderr"]).lower()
    assert not (venv / "bin" / "pytest").exists()
    site_packages = next(venv.glob("lib/python*/site-packages"))
    assert not list(site_packages.glob("pytest_fixture-*.dist-info"))

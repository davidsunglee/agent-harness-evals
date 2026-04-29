from pathlib import Path
import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def fixtures_dir(repo_root: Path) -> Path:
    return repo_root / "evals" / "tests" / "fixtures"


@pytest.fixture
def tmp_repo_root(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def synthetic_case_manifest(fixtures_dir: Path) -> Path:
    return fixtures_dir / "cases" / "test-case-001.json"


@pytest.fixture
def synthetic_case_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "cases" / "test-case-001"


@pytest.fixture
def fake_framework_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "fake-framework"

import json
import os
import socket
import sys
from pathlib import Path

import pytest

from evals.campaign import (
    LockBusyError,
    acquire_lock,
    current_campaign,
    eval_new,
    lock,
    release_lock,
)


# --- eval_new tests ---

def test_eval_new_creates_dir_manifest_and_symlink(tmp_repo_root):
    campaign_dir = eval_new(
        tmp_repo_root,
        frameworks=["fake"],
        cases=["test-case-001"],
        config_overrides={},
    )
    assert campaign_dir.exists()
    assert (campaign_dir / "manifest.json").exists()
    current_link = tmp_repo_root / "runs" / "CURRENT"
    assert current_link.is_symlink()
    assert current_link.resolve() == campaign_dir.resolve()


def test_eval_new_manifest_records_overrides(tmp_repo_root):
    campaign_dir = eval_new(
        tmp_repo_root,
        frameworks=["fake"],
        cases=["test-case-001"],
        config_overrides={"model": "foo"},
    )
    manifest = json.loads((campaign_dir / "manifest.json").read_text())
    overrides = manifest["config_overrides"]
    assert overrides["model"] == "foo"
    assert overrides["timeout_s"] is None
    assert overrides["max_steps"] is None


def test_eval_new_manifest_has_started_at(tmp_repo_root):
    campaign_dir = eval_new(
        tmp_repo_root,
        frameworks=[],
        cases=[],
        config_overrides={},
    )
    manifest = json.loads((campaign_dir / "manifest.json").read_text())
    assert "started_at" in manifest
    assert manifest["started_at"].endswith("Z")


def test_eval_new_manifest_has_frameworks_and_cases(tmp_repo_root):
    campaign_dir = eval_new(
        tmp_repo_root,
        frameworks=["fw1", "fw2"],
        cases=["case-a"],
        config_overrides={},
    )
    manifest = json.loads((campaign_dir / "manifest.json").read_text())
    assert manifest["frameworks"] == ["fw1", "fw2"]
    assert manifest["cases"] == ["case-a"]


# --- current_campaign tests ---

def test_current_campaign_returns_none_when_no_symlink(tmp_repo_root):
    result = current_campaign(tmp_repo_root)
    assert result is None


def test_current_campaign_returns_path_after_eval_new(tmp_repo_root):
    campaign_dir = eval_new(
        tmp_repo_root,
        frameworks=[],
        cases=[],
        config_overrides={},
    )
    result = current_campaign(tmp_repo_root)
    assert result is not None
    assert result.resolve() == campaign_dir.resolve()


# --- lock / acquire_lock / release_lock tests ---

def test_acquire_lock_writes_pid_hostname(tmp_path):
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    acquire_lock(campaign_dir, argv=["test"])
    lock_path = campaign_dir / ".lock"
    assert lock_path.exists()
    data = json.loads(lock_path.read_text())
    assert data["pid"] == os.getpid()
    assert data["hostname"] == socket.gethostname()


def test_acquire_lock_refuses_alive_same_host(tmp_path, monkeypatch):
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    lock_path = campaign_dir / ".lock"
    lock_data = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "started_at": "2026-01-01T00:00:00Z",
        "argv": ["test"],
    }
    lock_path.write_text(json.dumps(lock_data))
    with pytest.raises(LockBusyError, match=str(os.getpid())):
        acquire_lock(campaign_dir, argv=["test"])


def test_acquire_lock_reclaims_dead_same_host(tmp_path, capsys):
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    lock_path = campaign_dir / ".lock"
    # Use a very large PID that is extremely unlikely to exist
    dead_pid = 999999999
    lock_data = {
        "pid": dead_pid,
        "hostname": socket.gethostname(),
        "started_at": "2026-01-01T00:00:00Z",
        "argv": ["test"],
    }
    lock_path.write_text(json.dumps(lock_data))
    acquire_lock(campaign_dir, argv=["test"])
    data = json.loads(lock_path.read_text())
    assert data["pid"] == os.getpid()
    captured = capsys.readouterr()
    assert "stale" in captured.err.lower() or "warning" in captured.err.lower()


def test_acquire_lock_refuses_different_host(tmp_path):
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    lock_path = campaign_dir / ".lock"
    lock_data = {
        "pid": os.getpid(),
        "hostname": "other-host",
        "started_at": "2026-01-01T00:00:00Z",
        "argv": ["test"],
    }
    lock_path.write_text(json.dumps(lock_data))
    with pytest.raises(LockBusyError, match="other-host"):
        acquire_lock(campaign_dir, argv=["test"])


def test_acquire_lock_force_unlock_overrides_different_host(tmp_path, capsys):
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    lock_path = campaign_dir / ".lock"
    lock_data = {
        "pid": 12345,
        "hostname": "other-host",
        "started_at": "2026-01-01T00:00:00Z",
        "argv": ["test"],
    }
    lock_path.write_text(json.dumps(lock_data))
    acquire_lock(campaign_dir, argv=["test"], force_unlock=True)
    data = json.loads(lock_path.read_text())
    assert data["pid"] == os.getpid()
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower() or "overrid" in captured.err.lower()


def test_release_lock_removes_file(tmp_path):
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    lock_path = campaign_dir / ".lock"
    lock_path.write_text("{}")
    release_lock(campaign_dir)
    assert not lock_path.exists()


def test_release_lock_tolerates_missing(tmp_path):
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    release_lock(campaign_dir)  # should not raise


def test_lock_context_manager_releases_on_exception(tmp_path):
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    with pytest.raises(ValueError):
        with lock(campaign_dir, argv=["test"]):
            raise ValueError("deliberate")
    assert not (campaign_dir / ".lock").exists()

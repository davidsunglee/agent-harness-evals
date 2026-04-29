import json
import os
import tempfile
from pathlib import Path


def _cell_differs_from_campaign_config(cell_meta: dict, campaign_overrides: dict) -> bool:
    """Return True if any effective_config field was sourced from a cell-flag override."""
    sources = cell_meta.get("effective_config", {}).get("sources", {})
    return any(v == "cell-flag" for v in sources.values())


def render_report(campaign_dir: Path) -> str:
    campaign_dir = Path(campaign_dir)
    manifest = json.loads((campaign_dir / "manifest.json").read_text())

    ts = campaign_dir.name
    frameworks: list[str] = manifest["frameworks"]
    framework_set = set(frameworks)
    cases: list[str] = manifest["cases"]
    config_overrides: dict = manifest.get("config_overrides", {})

    lines: list[str] = []

    # Header
    lines.append(f"# Campaign {ts}")
    lines.append("")
    model = config_overrides.get("model") or "n/a"
    timeout_s = config_overrides.get("timeout_s") or "n/a"
    max_steps = config_overrides.get("max_steps") or "n/a"
    lines.append(f"Campaign config: model={model}, timeout_s={timeout_s}, max_steps={max_steps}")
    lines.append("")
    lines.append(f"Cases: {len(cases)} — {', '.join(cases)}")
    lines.append("")

    # Per-cell results table
    lines.append("## Per-cell results")
    lines.append("")
    lines.append("| framework | case | visible | hidden | edit_compl. | files | +/- lines | latency | tokens (i/o) | status |")
    lines.append("|-----------|------|---------|--------|-------------|-------|-----------|---------|--------------|--------|")

    fw_stats: dict[str, dict] = {
        fw: {"cases": 0, "ok": 0, "error": 0, "visible_pass": 0, "hidden_pass": 0}
        for fw in frameworks
    }
    notes_failures: list[tuple] = []
    notes_venv_mutations: list[str] = []

    for fw in frameworks:
        for case in cases:
            cell_dir = campaign_dir / fw / case
            meta_path = cell_dir / "meta.json"
            scoring_path = cell_dir / "scoring.json"

            meta: dict = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            scoring: dict = json.loads(scoring_path.read_text()) if scoring_path.exists() else {}

            differs = _cell_differs_from_campaign_config(meta, config_overrides)
            fw_label = f"{fw}*" if differs else fw

            visible = scoring.get("visible_test_outcome", "?")
            hidden = scoring.get("hidden_test_outcome", "?")

            minimality = scoring.get("minimality", {})
            files = minimality.get("changed_files", "?")
            added = minimality.get("changed_lines_added", 0)
            removed = minimality.get("changed_lines_removed", 0)
            lines_str = f"+{added}/-{removed}"

            latency_ms = meta.get("harness_latency_ms", 0)
            latency_str = f"{latency_ms / 1000:.1f}s"

            token_usage = scoring.get("token_usage", {})
            if token_usage:
                tokens_str = f"{token_usage.get('input', '?')}/{token_usage.get('output', '?')}"
            else:
                tokens_str = "-/-"

            ec = scoring.get("edit_constraint_compliance", {})
            ec_ok = (
                not ec.get("disallowed_violations")
                and not ec.get("allowed_violations")
                and not ec.get("over_max_changed_files")
            )
            edit_compl = "ok" if ec_ok else "fail"

            status = meta.get("status", "?")
            error_reason = meta.get("error_reason")
            if status == "error" and error_reason:
                status_str = f"error: {error_reason}"
            else:
                status_str = status

            lines.append(
                f"| {fw_label} | {case} | {visible} | {hidden} | {edit_compl} "
                f"| {files} | {lines_str} | {latency_str} | {tokens_str} | {status_str} |"
            )

            fw_stats[fw]["cases"] += 1
            if status == "ok":
                fw_stats[fw]["ok"] += 1
            elif status == "error":
                fw_stats[fw]["error"] += 1
            if visible == "pass":
                fw_stats[fw]["visible_pass"] += 1
            if hidden == "pass":
                fw_stats[fw]["hidden_pass"] += 1

            if status == "error":
                notes_failures.append((fw, case, error_reason, f"{fw}/{case}/stderr.log"))

            if meta.get("venv_mutated"):
                notes_venv_mutations.append(f"{fw}/{case}")

    lines.append("")

    # Per-framework summary table
    lines.append("## Per-framework summary")
    lines.append("")
    lines.append("| framework | cases | ok | error | visible_pass | hidden_pass |")
    lines.append("|-----------|-------|-------|-------|--------------|-------------|")
    for fw in frameworks:
        s = fw_stats[fw]
        lines.append(
            f"| {fw} | {s['cases']} | {s['ok']} | {s['error']} "
            f"| {s['visible_pass']} | {s['hidden_pass']} |"
        )
    lines.append("")

    # Notes section
    lines.append("## Notes")
    lines.append("")

    for fw, case, reason, rel_stderr in notes_failures:
        lines.append(f"- {fw}/{case}: {reason or 'unknown'} — [stderr.log]({rel_stderr})")

    # Setup failures: repo_root is two levels above campaign_dir (runs/<ts>)
    repo_root = campaign_dir.parent.parent
    setup_dir = repo_root / ".runs-cache" / "setup"
    if setup_dir.exists():
        for fail_file in sorted(setup_dir.glob("*.fail")):
            fw_name = fail_file.stem
            if fw_name not in framework_set:
                continue
            stderr_abs = setup_dir / f"{fw_name}.stderr.log"
            stderr_rel = os.path.relpath(stderr_abs, campaign_dir)
            lines.append(f"- setup failure: {fw_name} — [{stderr_rel}]({stderr_rel})")

    for cell in notes_venv_mutations:
        lines.append(f"- venv mutated: {cell}")

    lines.append("- trace_quality: n/a in v1 (capture-only)")

    return "\n".join(lines) + "\n"


def write_report(campaign_dir: Path) -> None:
    campaign_dir = Path(campaign_dir)
    content = render_report(campaign_dir).encode("utf-8")
    target = campaign_dir / "report.md"
    fd, tmp_path = tempfile.mkstemp(dir=str(campaign_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.rename(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise

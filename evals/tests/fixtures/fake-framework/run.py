#!/usr/bin/env python3
import glob
import json
import os
import sys
import time
from pathlib import Path

_DEFAULT_TRACE = {"steps": [], "tokens": {"input": 0, "output": 0}, "latency_ms": 0}


def _envelope(task_id: str, output, trace=None, error=None) -> dict:
    return {
        "task_id": task_id,
        "output": output,
        "trace": trace if trace is not None else _DEFAULT_TRACE,
        "error": error,
    }


def _valid_output(changed_files=None) -> dict:
    return {
        "root_cause": "fake",
        "summary": "fake",
        "changed_files": changed_files if changed_files is not None else [],
        "tests_run": [],
        "evidence": "fake",
        "confidence": 1.0,
    }


def main():
    request = json.load(sys.stdin)
    task_id = request["task_id"]
    repo_path = request.get("input", {}).get("repo_path", ".")

    behavior = os.environ.get("FAKE_BEHAVIOR", "success-noop")

    if behavior == "success-noop":
        print(json.dumps(_envelope(task_id, _valid_output())))
        sys.exit(0)

    elif behavior == "success-fix":
        arith = Path(repo_path) / "test_case_001" / "arith.py"
        arith.write_text(arith.read_text().replace("return a - b", "return a + b"))
        print(json.dumps(_envelope(task_id, _valid_output(["test_case_001/arith.py"]))))
        sys.exit(0)

    elif behavior == "hang":
        time.sleep(10**9)

    elif behavior == "crash":
        sys.stderr.write("boom\n")
        sys.exit(1)

    elif behavior == "crash-with-error-envelope":
        print(json.dumps(_envelope(task_id, None, error={"message": "intentional crash"})))
        sys.exit(1)

    elif behavior == "crash-with-bad-json":
        print(json.dumps({"task_id": "x"}))
        sys.exit(1)

    elif behavior == "garbage":
        sys.stdout.write("not-json-at-all")
        sys.exit(0)

    elif behavior == "empty":
        sys.exit(0)

    elif behavior == "oversize":
        sys.stdout.write("a" * (9 * 1024 * 1024))
        sys.stdout.flush()
        sys.exit(0)

    elif behavior == "missing-field":
        # Missing required 'trace' and 'error' keys
        print(json.dumps({"task_id": task_id, "output": {"changed_files": []}}))
        sys.exit(0)

    elif behavior == "forbidden-field":
        # output.fixed is forbidden by task-spec; envelope-valid, agent-output invalid
        out = _valid_output()
        out["fixed"] = True
        print(json.dumps(_envelope(task_id, out)))
        sys.exit(0)

    elif behavior == "disallowed-edit":
        # tests/test_arith.py is in the default disallowed list
        test_file = Path(repo_path) / "tests" / "test_arith.py"
        test_file.write_text(test_file.read_text() + "\n# edited by fake-framework\n")
        print(json.dumps(_envelope(task_id, _valid_output(["tests/test_arith.py"]))))
        sys.exit(0)

    elif behavior == "over-max-files":
        # Create 6 files (> default max_changed_files = 5)
        changed = []
        for i in range(6):
            f = Path(repo_path) / "test_case_001" / f"extra_{i}.py"
            f.write_text(f"# extra file {i}\n")
            changed.append(f"test_case_001/extra_{i}.py")
        print(json.dumps(_envelope(task_id, _valid_output(changed))))
        sys.exit(0)

    elif behavior == "noisy-stderr":
        sys.stderr.write("X" * (6 * 1024 * 1024 + 1))
        sys.stderr.flush()
        print(json.dumps(_envelope(task_id, _valid_output())))
        sys.exit(0)

    elif behavior == "mutate-venv":
        uv_env = os.environ.get("UV_PROJECT_ENVIRONMENT", "")
        if uv_env:
            matches = glob.glob(os.path.join(uv_env, "lib", "*", "site-packages"))
            if matches:
                marker_dir = Path(matches[0]) / "__fake_marker__.dist-info"
                marker_dir.mkdir(exist_ok=True)
                (marker_dir / "METADATA").write_text("Metadata-Version: 2.1\nName: fake-marker\n")
        print(json.dumps(_envelope(task_id, _valid_output())))
        sys.exit(0)

    elif behavior == "noisy-test-output":
        # success-fix + conftest that prints > 6 MiB per test
        arith = Path(repo_path) / "test_case_001" / "arith.py"
        arith.write_text(arith.read_text().replace("return a - b", "return a + b"))
        conftest = Path(repo_path) / "tests" / "conftest.py"
        conftest.write_text(
            "import sys\n\n"
            "def pytest_runtest_setup(item):\n"
            "    sys.stdout.write('Y' * (6 * 1024 * 1024 + 1))\n"
            "    sys.stdout.flush()\n"
        )
        print(json.dumps(_envelope(task_id, _valid_output(["test_case_001/arith.py", "tests/conftest.py"]))))
        sys.exit(0)

    else:
        sys.stderr.write(f"Unknown FAKE_BEHAVIOR: {behavior}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

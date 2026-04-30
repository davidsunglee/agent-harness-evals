"""Framework-local regression tests for the DeepAgents adapter.

Runnable from `frameworks/deepagents/` via:
    uv run --quiet python -m unittest -q

Stdlib-only (unittest); no pytest dependency added to this framework.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import adapter  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402


class FilesystemRootingTest(unittest.TestCase):
    """Regression for: filesystem tools must be rooted at input.repo_path.

    Reproducer (pre-fix): LocalShellBackend(root_dir=repo, virtual_mode=False)
    accepts host absolute paths and '..' traversal, allowing reads outside the
    case worktree.
    """

    @staticmethod
    def _read_content(result) -> str:
        """Pull the content string out of a ReadResult, regardless of whether
        file_data is exposed as a dict (TypedDict) or attr-bearing object."""
        file_data = getattr(result, "file_data", None)
        if file_data is None:
            return ""
        if isinstance(file_data, dict):
            return file_data.get("content") or ""
        return getattr(file_data, "content", "") or ""

    def test_absolute_host_path_does_not_escape_repo(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            parent_path = Path(parent)
            outside_file = parent_path / "outside-file"
            outside_file.write_text("SENSITIVE-OUTSIDE")
            repo = parent_path / "repo"
            repo.mkdir()

            backend = adapter._build_backend(repo_path=str(repo))
            result = backend.read(str(outside_file))

            content = self._read_content(result)
            self.assertNotIn(
                "SENSITIVE-OUTSIDE",
                content,
                msg="filesystem backend leaked content from outside repo_path",
            )

    def test_dotdot_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            parent_path = Path(parent)
            outside_file = parent_path / "outside-file"
            outside_file.write_text("SENSITIVE-OUTSIDE")
            repo = parent_path / "repo"
            repo.mkdir()

            backend = adapter._build_backend(repo_path=str(repo))
            # Rooted backends may reject traversal either by raising or by
            # returning a ReadResult with an error and no file_data.
            content = ""
            error = None
            try:
                result = backend.read("../outside-file")
            except ValueError as exc:
                error = str(exc)
            else:
                content = self._read_content(result)
                error = result.error
            self.assertNotIn(
                "SENSITIVE-OUTSIDE",
                content,
                msg="filesystem backend allowed '..' traversal outside repo_path",
            )
            self.assertIsNotNone(
                error,
                msg="expected '..' traversal to be rejected with an error",
            )


class TraceAssociationTest(unittest.TestCase):
    """Regression for: ToolMessage results must be matched to the correct
    prior tool call when an AIMessage emits multiple tool calls.
    """

    def test_multiple_tool_calls_in_single_ai_message(self) -> None:
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/a.py"},
                    "id": "call-1",
                    "type": "tool_call",
                },
                {
                    "name": "execute",
                    "args": {"command": "ls"},
                    "id": "call-2",
                    "type": "tool_call",
                },
            ],
        )
        tm1 = ToolMessage(
            content="contents-of-a",
            tool_call_id="call-1",
            name="read_file",
        )
        tm2 = ToolMessage(
            content="ls-output",
            tool_call_id="call-2",
            name="execute",
        )

        trace = adapter._messages_to_trace([ai, tm1, tm2], latency_ms=100)
        steps = trace["steps"]

        self.assertEqual(
            len(steps), 2, msg=f"expected 2 steps, got {len(steps)}: {steps}"
        )
        self.assertEqual(steps[0]["kind"], "tool_call")
        self.assertEqual(steps[0]["name"], "read_file")
        self.assertEqual(steps[0]["args"], {"file_path": "/a.py"})
        self.assertEqual(steps[0]["result"], {"content": "contents-of-a"})

        self.assertEqual(steps[1]["kind"], "tool_call")
        self.assertEqual(steps[1]["name"], "execute")
        self.assertEqual(steps[1]["args"], {"command": "ls"})
        self.assertEqual(steps[1]["result"], {"content": "ls-output"})

    def test_results_arrive_out_of_order_match_by_id(self) -> None:
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/a.py"},
                    "id": "call-A",
                    "type": "tool_call",
                },
                {
                    "name": "read_file",
                    "args": {"file_path": "/b.py"},
                    "id": "call-B",
                    "type": "tool_call",
                },
            ],
        )
        # Results delivered B-then-A on purpose.
        tm_b = ToolMessage(content="B", tool_call_id="call-B", name="read_file")
        tm_a = ToolMessage(content="A", tool_call_id="call-A", name="read_file")

        trace = adapter._messages_to_trace([ai, tm_b, tm_a], latency_ms=10)
        steps = trace["steps"]
        self.assertEqual(len(steps), 2)
        # step 0 was call-A (file_path /a.py); must receive content "A"
        self.assertEqual(steps[0]["args"], {"file_path": "/a.py"})
        self.assertEqual(steps[0]["result"], {"content": "A"})
        self.assertEqual(steps[1]["args"], {"file_path": "/b.py"})
        self.assertEqual(steps[1]["result"], {"content": "B"})


class ShellEnvSecretIsolationTest(unittest.TestCase):
    """Regression for: shell env must not forward provider/API secrets.

    Reproducer (pre-fix): _build_shell_env starts from os.environ.copy() and
    forwards everything — including ANTHROPIC_API_KEY and other provider tokens —
    into LocalShellBackend. The model-controlled `execute` tool can then read
    those secrets and surface them in traces/artifacts.
    """

    _SECRET_KEYS_THAT_MUST_BE_DROPPED = (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GITHUB_TOKEN",
        "HF_TOKEN",
        "GCP_SERVICE_ACCOUNT_KEY",
    )

    def _patched_environ(self) -> dict[str, str]:
        return {
            "ANTHROPIC_API_KEY": "sk-ant-SENSITIVE-REVIEW-SECRET",
            "OPENAI_API_KEY": "sk-openai-SENSITIVE",
            "AWS_ACCESS_KEY_ID": "AKIA-SENSITIVE",
            "AWS_SECRET_ACCESS_KEY": "aws-secret-SENSITIVE",
            "AWS_SESSION_TOKEN": "aws-session-SENSITIVE",
            "GITHUB_TOKEN": "ghp_SENSITIVE",
            "HF_TOKEN": "hf_SENSITIVE",
            "GCP_SERVICE_ACCOUNT_KEY": "gcp-SENSITIVE",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp/fake-home",
            "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color",
        }

    def test_provider_secrets_not_in_shell_env(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            repo = Path(parent) / "repo"
            repo.mkdir()
            with mock.patch.dict(os.environ, self._patched_environ(), clear=True):
                env = adapter._build_shell_env(repo_path=str(repo))

            for key in self._SECRET_KEYS_THAT_MUST_BE_DROPPED:
                self.assertNotIn(
                    key,
                    env,
                    msg=f"{key} must not be forwarded into the shell tool env",
                )
            joined_values = "\n".join(env.values())
            self.assertNotIn(
                "SENSITIVE",
                joined_values,
                msg=f"shell env still contains a SENSITIVE marker: {env}",
            )

    def test_safe_keys_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            repo = Path(parent) / "repo"
            repo.mkdir()
            with mock.patch.dict(os.environ, self._patched_environ(), clear=True):
                env = adapter._build_shell_env(repo_path=str(repo))

            self.assertIn("PATH", env)
            self.assertIn("HOME", env)
            self.assertEqual(env["HOME"], "/tmp/fake-home")
            self.assertEqual(env["LANG"], "en_US.UTF-8")
            self.assertEqual(env["TERM"], "xterm-256color")
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertIn(str(repo.resolve()), env["PYTHONPATH"])

    def test_case_venv_still_prepended_to_path(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            repo = Path(parent) / "repo"
            repo.mkdir()
            case_venv = Path(parent) / "case-venv"
            (case_venv / "bin").mkdir(parents=True)
            extra = self._patched_environ()
            extra["AGENT_HARNESS_CASE_VENV"] = str(case_venv)
            with mock.patch.dict(os.environ, extra, clear=True):
                env = adapter._build_shell_env(repo_path=str(repo))

            self.assertEqual(env["UV_PROJECT_ENVIRONMENT"], str(case_venv.resolve()))
            self.assertEqual(env["UV_NO_SYNC"], "1")
            self.assertTrue(
                env["PATH"].startswith(str(case_venv.resolve() / "bin")),
                msg=f"case venv bin must be first on PATH; got {env['PATH']!r}",
            )

    def test_backend_execute_does_not_inherit_anthropic_api_key(self) -> None:
        """End-to-end: a shell command executed via the backend cannot see
        ANTHROPIC_API_KEY because the adapter scrubs it from the shell env."""
        with tempfile.TemporaryDirectory() as parent:
            repo = Path(parent) / "repo"
            repo.mkdir()
            with mock.patch.dict(
                os.environ,
                {**self._patched_environ()},
                clear=True,
            ):
                backend = adapter._build_backend(repo_path=str(repo))
                result = backend.execute('printf %s "${ANTHROPIC_API_KEY:-MISSING}"')

            output = getattr(result, "output", "") or ""
            stdout = getattr(result, "stdout", "") or ""
            combined = f"{output}\n{stdout}"
            self.assertNotIn(
                "SENSITIVE",
                combined,
                msg=f"shell execute leaked ANTHROPIC_API_KEY: {combined!r}",
            )


if __name__ == "__main__":
    unittest.main()

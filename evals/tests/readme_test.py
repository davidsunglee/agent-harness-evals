from pathlib import Path


def test_readme_documents_shell_command_preservation() -> None:
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text()

    assert "/bin/sh -c" in text
    assert "exactly as declared" in text
    assert "redirects" in text

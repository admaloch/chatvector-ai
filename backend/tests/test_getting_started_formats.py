"""Getting-started copy stays aligned with the supported upload formats."""

from pathlib import Path


def test_getting_started_lists_docx_with_other_supported_formats():
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / "documentation/content/getting-started.md").read_text()
    sentence = next(line for line in text.splitlines() if "upload a PDF" in line)
    for token in ("PDF", "plain-text", "Markdown", "HTML", "DOCX", ".docx"):
        assert token in sentence

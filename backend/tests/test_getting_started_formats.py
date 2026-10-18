"""Getting-started copy stays aligned with the supported upload formats."""

from tests.repo_layout import repo_file


def test_getting_started_lists_docx_with_other_supported_formats():
    text = repo_file("documentation", "content", "getting-started.md").read_text()
    sentence = next(line for line in text.splitlines() if "upload a PDF" in line)
    for token in ("PDF", "plain-text", "Markdown", "HTML", "DOCX", ".docx"):
        assert token in sentence

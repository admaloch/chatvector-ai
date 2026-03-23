# PR: Improve Text Cleaning for PDF Extraction Artifacts

**Branch:** `fix/text-cleaning-improvements`
**Base:** `main`

---

## Summary

Rewrites `backend/services/text_cleaning_service.py` to use a simple, reliable "nuclear flattening" approach that converts all line breaks to spaces — producing clean, flat prose strings ready for chunking and embedding.

Also fixes two bugs present in the previous implementation.

---

## Problem

PDF text extraction produces artifacts that broke downstream chunking and RAG retrieval:

- Words split across lines (`"users\nto\nhave\nconversational"`)
- Bullet characters (`●`, `•`, `▪`, `◦`) left in extracted text
- Null bytes and other control characters passing through silently
- Tab characters not normalized (only spaces were collapsed)

---

## Changes

### `backend/services/text_cleaning_service.py`

Clean rewrite with a clear, ordered pipeline:

1. **NFKC normalization** — expands ligatures (`ﬁ→fi`), normalises fullwidth chars, converts NBSP to regular space
2. **Control character removal** — strips null bytes, bell, backspace, etc. *(bug fix — these were silently passing through before)*
3. **Bullet removal** — strips `●`, `•`, `▪`, `▸`, `◦`, `‣`, and similar Unicode bullet characters
4. **Soft hyphen removal + hyphenated line-break rejoining** — fixes PDF word-wrap (`"connec-\ntion"→"connection"`)
5. **Line-break flattening** — all `\n` and `\r` replaced with a single space
6. **Whitespace normalization** — collapses all space/tab runs to a single space *(bug fix — previously only collapsed spaces, not tabs)*

### `backend/tests/test_text_cleaning_service.py`

Full rewrite of the test suite (46 tests) to match the nuclear approach:

- `TestEdgeCases` — empty input, whitespace-only
- `TestUnicodeNormalization` — ligatures, NBSP, fullwidth digits
- `TestControlCharacterRemoval` — null bytes, bell/backspace, newlines→spaces, tabs→spaces
- `TestSoftHyphenRemoval` — soft hyphen stripped, regular hyphen preserved
- `TestHyphenatedLineBreaks` — PDF word-wrap rejoined; standalone hyphens preserved
- `TestLineBreakFlattening` — LF, CR, CRLF, multiple newlines, blank lines, trailing whitespace all become spaces
- `TestWhitespaceNormalization` — spaces, tabs, mixed runs collapsed to single space
- `TestBulletRemoval` — all supported bullet characters removed
- `TestPDFReflowArtifacts` — word-per-line, uppercase, mixed-case fragments all merged
- `TestRealWorldPatterns` — end-to-end PDF page, TXT dump, ligature+hyphen combo, control chars

---

## Test Results

```
46 passed in 0.04s
```

---

## Notes

- `clean_text()` is already called before chunking in both paths of `ingestion_pipeline.py` (lines 167 and 275) — no pipeline changes needed.
- The nuclear approach intentionally does not preserve paragraph structure. For this project's RAG use case, flat prose per chunk is the correct shape for embedding.

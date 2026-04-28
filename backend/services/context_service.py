import logging
import os

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS: int = int(os.getenv("MAX_CONTEXT_CHARS", "32000"))


def build_context_from_chunks(chunks: list) -> str:
    """
    Combine chunk texts into a single context string for the LLM.

    Each chunk is prefixed with a source label so the model can cite
    the originating file and page in its answer.

    Chunks are dropped (whole, from the end) if the total would exceed
    MAX_CONTEXT_CHARS, preserving formatting of all included chunks.
    """
    parts: list[str] = []
    total_chars = 0
    separator = "\n\n"
    sep_len = len(separator)

    for i, chunk in enumerate(chunks):
        label = f"[Source: {chunk.file_name or 'unknown'}"
        if chunk.page_number is not None:
            label += f", page {chunk.page_number}"
        label += "]"
        part = f"{label}\n{chunk.chunk_text or ''}"

        addition = (sep_len + len(part)) if parts else len(part)
        if total_chars + addition > MAX_CONTEXT_CHARS:
            if not parts:
                # Single chunk exceeds cap; include it rather than returning empty context.
                parts.append(part)
                dropped = len(chunks) - i - 1
                used = len(part)
            else:
                dropped = len(chunks) - i
                used = total_chars
            logger.warning(
                "Context truncated: dropped %d of %d chunks to stay within "
                "MAX_CONTEXT_CHARS=%d (used %d chars)",
                dropped,
                len(chunks),
                MAX_CONTEXT_CHARS,
                used,
            )
            break

        parts.append(part)
        total_chars += addition

    context = separator.join(parts)
    logger.info("Constructed context of length %d", len(context))
    return context

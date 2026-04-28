"""Tests for system prompt loading and LLM generation config wiring."""

import pytest

import services.answer_service as answer_service
import services.providers as providers_mod


def test_load_system_prompt_returns_stripped_content(tmp_path, monkeypatch):
    prompt_file = tmp_path / "custom_system.txt"
    prompt_file.write_text("  line one\nline two  \n", encoding="utf-8")
    monkeypatch.setattr(answer_service.config, "SYSTEM_PROMPT_PATH", str(prompt_file))
    monkeypatch.setattr(answer_service, "_SYSTEM_PROMPT", None)

    assert answer_service._get_system_prompt() == "line one\nline two"


def test_load_system_prompt_missing_file_raises_clear_file_not_found(tmp_path, monkeypatch):
    missing = tmp_path / "does_not_exist.txt"
    monkeypatch.setattr(answer_service.config, "SYSTEM_PROMPT_PATH", str(missing))
    monkeypatch.setattr(answer_service, "_SYSTEM_PROMPT", None)

    with pytest.raises(FileNotFoundError) as exc_info:
        answer_service._get_system_prompt()

    msg = str(exc_info.value)
    assert "System prompt file not found" in msg
    assert "SYSTEM_PROMPT_PATH" in msg


@pytest.mark.asyncio
async def test_generate_answer_passes_temperature_and_max_tokens_to_provider(
    monkeypatch,
):
    """Verify that generate_answer forwards config values to the provider."""
    captured: dict = {}

    class _CapturingProvider:
        async def generate(self, prompt, *, system_instruction, temperature, max_output_tokens):
            captured["prompt"] = prompt
            captured["system_instruction"] = system_instruction
            captured["temperature"] = temperature
            captured["max_output_tokens"] = max_output_tokens
            return "mocked answer"

    providers_mod._llm_provider = _CapturingProvider()
    monkeypatch.setattr(answer_service.config, "LLM_TEMPERATURE", 0.7)
    monkeypatch.setattr(answer_service.config, "LLM_MAX_OUTPUT_TOKENS", 512)

    try:
        result = await answer_service.generate_answer("What?", "some context")
    finally:
        providers_mod._llm_provider = None

    assert result == "mocked answer"
    assert captured["prompt"] == "CONTEXT:\nsome context\n\nQUESTION:\nWhat?"
    assert captured["system_instruction"] == answer_service._SYSTEM_PROMPT
    assert captured["temperature"] == 0.7
    assert captured["max_output_tokens"] == 512

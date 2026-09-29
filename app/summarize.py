"""Generate notes from a transcript, driven by a Claude Skill."""

from __future__ import annotations

import logging

import anthropic

from . import config, formats, skills

log = logging.getLogger(__name__)

FRAMING = (
    "You are a meeting-notes assistant. You will be given a diarized transcript "
    "and a skill that defines exactly how to turn it into notes. Follow the "
    "skill's structure and conventions precisely — it takes precedence over any "
    "general habits you have about summarizing.\n\n"
    "The transcript comes from automatic speech recognition, so expect "
    "misrecognized words and imperfect speaker boundaries. Where a name or term "
    "is clearly garbled but recoverable from context, use the corrected form. "
    "Never invent decisions, owners, or commitments that are not in the "
    "transcript; if something important is ambiguous, say so explicitly rather "
    "than guessing.\n\n"
    "Output Markdown only, with no preamble and no closing commentary.\n\n"
    "=== SKILL ===\n"
)


class SummarizeError(RuntimeError):
    pass


def _client() -> anthropic.Anthropic:
    # A zero-arg client also picks up an `ant auth login` profile, so an unset
    # ANTHROPIC_API_KEY is not necessarily an error.
    if config.ANTHROPIC_API_KEY:
        return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return anthropic.Anthropic()


def _user_message(session: dict, utterances: list[dict]) -> str:
    speakers = sorted({u["speaker"] for u in utterances})
    duration = session.get("duration_s") or 0
    header = (
        f"Title: {session.get('title', 'Untitled')}\n"
        f"Date: {session.get('created_at', '')}\n"
        f"Duration: {int(duration // 60)} min {int(duration % 60)} s\n"
        f"Participants ({len(speakers)}): {', '.join(speakers)}\n"
    )
    return f"{header}\n=== TRANSCRIPT ===\n\n{formats.to_markdown(utterances)}"


def run(session: dict, utterances: list[dict], skill_id: str) -> str:
    if not utterances:
        raise SummarizeError("Transcript is empty — nothing to summarize.")

    skill = skills.load_skill(skill_id)
    client = _client()

    try:
        # Streaming: a long transcript plus a long response would otherwise risk
        # an HTTP timeout at this max_tokens.
        with client.beta.messages.stream(
            model=config.CLAUDE_MODEL,
            max_tokens=64000,
            # The skill is stable across runs and `system` renders before
            # `messages`, so caching here means a re-summarize of the same
            # skill reads the prefix from cache instead of re-billing it.
            system=[{
                "type": "text",
                "text": FRAMING + skill["text"],
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": _user_message(session, utterances)}],
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()
    except anthropic.APIStatusError as exc:
        raise SummarizeError(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise SummarizeError(f"Could not reach the Claude API: {exc}") from exc

    # Check the stop reason before reading content — a refusal returns HTTP 200.
    if message.stop_reason == "refusal":
        category = getattr(message.stop_details, "category", None)
        raise SummarizeError(
            "Claude declined to summarize this transcript"
            + (f" (category: {category})" if category else "")
            + ". The fallback model declined as well."
        )

    usage = message.usage
    log.info(
        "Summarized %s: in=%s cache_read=%s out=%s model=%s",
        session.get("id"), usage.input_tokens,
        getattr(usage, "cache_read_input_tokens", 0),
        usage.output_tokens, message.model,
    )

    text = "\n".join(
        block.text for block in message.content if block.type == "text"
    ).strip()
    if not text:
        raise SummarizeError("Claude returned no text content.")
    return text

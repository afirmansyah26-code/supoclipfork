"""Generate social post title, hook title + description from a clip via the configured LLM."""

import asyncio
import json
import logging

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider

from ..config import get_config
from ..ai import _split_llm_name

logger = logging.getLogger(__name__)

POST_TITLE_PROMPT = """You are a social media expert who creates viral content for TikTok, Reels, and Shorts.
You will receive a JSON object with the clip's headline, transcript excerpt, and hook type.
Generate three things: an on-screen hook title, a social post title, and a description with hashtags.

Rules:
- Hook title: 3-9 words, plain text, a bold on-screen headline burned into the top of the clip. Make a scrolling viewer stop: a bold claim, curiosity gap, number, or stakes taken directly from the segment. No hashtags, no emojis, no quotes. Written in the same language as the transcript.
- Post title: max 100 characters, engaging and clickable, written in the same language as the transcript.
- Description: 2-3 sentences with relevant hashtags, written in the same language as the transcript, viral-worthy.
- Use only facts from the transcript and headline. Never invent quotes, statistics, or promises.
- Return ONLY valid JSON in this exact format: {"hook_title": "...", "title": "...", "description": "..."}
- No markdown, no code fences, no commentary."""


class PostDraft(BaseModel):
    hook_title: str
    title: str
    description: str


async def generate_post_draft(clip: dict) -> dict:
    """Generate a hook title + social post title + description from a clip's data.

    Uses the same LLM provider configured for the app (ollama → 9router in this
    deployment). Falls back gracefully if the model returns non-JSON.
    """
    runtime_config = get_config()
    provider, model_name = _split_llm_name(runtime_config.llm)

    if provider != "ollama" or not model_name:
        raise RuntimeError(
            "Post generation uses the ollama LLM provider. "
            f"Current LLM is '{runtime_config.llm}'. Set LLM=ollama:<model>."
        )

    model = OllamaModel(
        model_name,
        provider=OllamaProvider(
            base_url=runtime_config.resolve_ollama_base_url(),
            api_key=runtime_config.ollama_api_key,
        ),
    )

    agent = Agent(
        model,
        output_type=PostDraft,
        system_prompt=POST_TITLE_PROMPT,
        model_settings={"max_tokens": 600},
        retries=1,
    )

    prompt = json.dumps({
        "headline": (clip.get("hook_title") or "")[:255],
        "hook_type": clip.get("hook_type") or "",
        "transcript": (clip.get("text") or "")[:8000],
    }, ensure_ascii=False)

    result = await asyncio.wait_for(agent.run(prompt), timeout=60)
    draft = result.output

    if not draft.title.strip() and not draft.description.strip() and not draft.hook_title.strip():
        raise ValueError("The model returned an empty response.")

    return {
        "hook_title": draft.hook_title.strip()[:200],
        "title": draft.title.strip()[:100],
        "description": draft.description.strip()[:2200],
    }

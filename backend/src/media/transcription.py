"""Transcription helpers for the video pipeline."""

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
import assemblyai as aai
from ..config import get_config
import httpx
import json
import re
import time
from .common import (
    ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS,
    ANALYSIS_LONG_UTTERANCE_MAX_WORDS,
    ANALYSIS_SEGMENT_MAX_DURATION_MS,
    ANALYSIS_SEGMENT_MAX_WORDS,
    ANALYSIS_SEGMENT_MIN_WORDS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS,
    TRANSCRIPT_CACHE_SCHEMA_VERSION,
    _WHISPER_AVAILABLE,
    _WHISPER_MODEL_CACHE,
    _whisper,
    logger,
)
from .ffmpeg import (
    run_ffmpeg_command,
    select_audio_stream_index,
)


def _prepare_audio_for_transcription(video_path: Path) -> Path:
    """Extract a compact audio-only file for transcription."""
    audio_path = video_path.with_name(f"{video_path.stem}.transcription.mp3")
    if audio_path.exists() and audio_path.stat().st_size > 0:
        return audio_path

    # Transcribe the same track the renderer cuts, not ffmpeg's implicit pick.
    audio_stream = select_audio_stream_index(video_path)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        *(["-map", f"0:a:{audio_stream}"] if audio_stream is not None else []),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    try:
        result = run_ffmpeg_command(command, timeout=900)
    except FileNotFoundError:
        logger.warning(
            "ffmpeg is not available; falling back to source video for transcription"
        )
        return video_path

    if result.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        logger.warning(
            "Failed to extract transcription audio with ffmpeg; falling back to source video"
        )
        return video_path

    logger.info(
        "Prepared transcription audio: %s (%.2f MB)",
        audio_path,
        audio_path.stat().st_size / (1024 * 1024),
    )
    return audio_path


def _submit_and_wait_for_assemblyai_transcript(
    transcriber,
    media_path: Path,
    config_obj,
    timeout_seconds: int,
):
    """Submit a transcript job and poll with a total timeout."""
    # Uploads are safe to retry: they do not create a paid transcript. Keep the
    # transcript POST outside this loop because a timeout may hide its success.
    for attempt in range(1, 4):
        try:
            audio_url = transcriber.upload_file(str(media_path))
            break
        except (httpx.TransportError, aai.AssemblyAIError) as error:
            status = getattr(error, "status_code", None)
            retryable = isinstance(error, httpx.TransportError) or status in {408, 429, 500, 502, 503, 504}
            if not retryable:
                raise
            if attempt == 3:
                raise RuntimeError(
                    "AssemblyAI audio upload unavailable after 3 attempts"
                    + (f" (HTTP {status})." if status else ".")
                ) from None
            logger.warning("AssemblyAI audio upload failed (HTTP %s); retrying upload (%s/3)", status, attempt)
            time.sleep(2.0 ** attempt)
    submitted = transcriber.submit(audio_url, config=config_obj)
    if not submitted.id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")

    logger.info("AssemblyAI transcript submitted: %s", submitted.id)
    deadline = time.monotonic() + timeout_seconds
    next_log_at = 0.0
    consecutive_failures = 0

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"AssemblyAI transcript {submitted.id} did not complete within {timeout_seconds}s"
            )
        # The SDK poller uses the upload timeout (often 900s) for every GET.
        # Bound each poll without changing the shared client's timeout.
        try:
            response = submitted._client.http_client.get(  # noqa: SLF001
                f"{aai.api.ENDPOINT_TRANSCRIPT}/{submitted.id}",
                timeout=min(30.0, remaining),
            )
            response.raise_for_status()
            response = aai.types.TranscriptResponse(**response.json())
            consecutive_failures = 0
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code not in {408, 429, 500, 502, 503, 504}:
                raise
            consecutive_failures += 1
            logger.warning("AssemblyAI status polling unavailable; retrying the existing transcript")
            time.sleep(min(30.0, 2.0 ** min(consecutive_failures, 4),
                           max(0.0, deadline - time.monotonic())))
            continue
        transcript = aai.Transcript.from_response(
            client=submitted._client,  # noqa: SLF001
            response=response,
        )

        if transcript.status in (
            aai.TranscriptStatus.completed,
            aai.TranscriptStatus.error,
        ):
            return transcript

        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError(
                f"AssemblyAI transcript {submitted.id} did not complete within {timeout_seconds}s"
            )

        if now >= next_log_at:
            logger.info(
                "AssemblyAI transcript %s still %s",
                submitted.id,
                transcript.status,
            )
            next_log_at = now + 30

        time.sleep(min(aai.settings.polling_interval, max(0.0, deadline - now)))


def _assemblyai_speech_models_value(speech_model: str) -> List[str]:
    """Prefer Universal-3.5 Pro; retain Universal-2 for unsupported languages.

    Explicit low-cost aliases remain available for self-hosted configurations.
    Legacy quality aliases upgrade to the current Pro model.
    """
    normalized = (speech_model or "universal-3-5-pro").strip().lower()
    if normalized in {"nano", "universal-2"}:
        return ["universal-2"]
    return ["universal-3-5-pro", "universal-2"]


def _get_whisper_model(model_name: str = "base"):
    """Load and cache a Whisper model by name."""
    if not _WHISPER_AVAILABLE:
        raise RuntimeError(
            "Whisper is not installed. Install it with: uv add openai-whisper"
        )
    if model_name not in _WHISPER_MODEL_CACHE:
        logger.info("Loading Whisper model: %s", model_name)
        _WHISPER_MODEL_CACHE[model_name] = _whisper.load_model(model_name)
    return _WHISPER_MODEL_CACHE[model_name]


def transcribe_with_whisper(
    video_path: Path, model_name: str = "base", language: Optional[str] = None
) -> Dict[str, Any]:
    """Transcribe video using local Whisper with word-level timestamps.

    ``language`` None lets Whisper detect it; AssemblyAI-style codes such as
    ``en_us`` are reduced to the ISO 639-1 base Whisper expects.
    """
    audio_path = _prepare_audio_for_transcription(video_path)
    model = _get_whisper_model(model_name)
    whisper_language = language.split("_", 1)[0] if language else None
    logger.info(
        "Starting Whisper transcription with model: %s (language: %s)",
        model_name,
        whisper_language or "auto",
    )
    return model.transcribe(str(audio_path), word_timestamps=True, language=whisper_language)


def _whisper_result_to_transcript_data(whisper_result: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a Whisper result dict to the standard transcript-cache format.

    Whisper reports timings in seconds; the cache (and AssemblyAI path) stores
    milliseconds, so each timestamp is multiplied by 1000 here.
    """
    words_data: List[Dict[str, Any]] = []
    utterances_data: List[Dict[str, Any]] = []

    for segment in whisper_result.get("segments") or []:
        seg_words = [
            {
                "text": w.get("word", w.get("text", "")),
                "start": int(w["start"] * 1000) if isinstance(w.get("start"), float) else int(w.get("start", 0)),
                "end": int(w["end"] * 1000) if isinstance(w.get("end"), float) else int(w.get("end", 0)),
                "confidence": w.get("probability", w.get("confidence", 1.0)),
                "speaker": None,
            }
            for w in segment.get("words") or []
        ]
        utterances_data.append(
            {
                "text": segment.get("text", ""),
                "start": int(segment["start"] * 1000) if "start" in segment else 0,
                "end": int(segment["end"] * 1000) if "end" in segment else 0,
                "speaker": None,
                "words": seg_words,
            }
        )
        words_data.extend(seg_words)

    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words_data,
        "utterances": utterances_data,
        "text": whisper_result.get("text", ""),
        "language_code": whisper_result.get("language"),
    }


def _youtube_caption_language_pref(runtime_config) -> list[str]:
    """Build a preferred caption-language list for yt-dlp.

    Manual captions are always preferred over auto-generated ones; the list
    tries the spoken language first (when known), then English, then anything.
    """
    spoken = (getattr(runtime_config, "transcription_language", "") or "").split("_", 1)[0].lower()
    prefs: list[str] = []
    if spoken:
        prefs.extend([spoken, f"{spoken}-orig"])
    if "en" not in prefs:
        prefs.append("en")
    return prefs


def _download_youtube_captions(video_url: str, video_id: str, temp_dir: Path) -> Optional[Path]:
    """Download the best YouTube caption track and return its path.

    Tries manual captions first across the preferred languages, then falls
    back to auto-generated captions. json3 is preferred (it carries word
    offsets); vtt is the fallback.
    """
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp is required for YouTube caption extraction")
        return None

    runtime_config = get_config()
    lang_prefs = _youtube_caption_language_pref(runtime_config)

    # Prefer manual captions across all languages first, then auto captions.
    sub_langs: list[str] = []
    for lang in lang_prefs:
        sub_langs.append(lang)
    # Ask yt-dlp for both manual and auto subs for every preferred language.
    # yt-dlp treats "en" as "en.*" when writesubtitles+writeautomaticsub are set.

    base_tmpl = str(temp_dir / video_id)
    formats = ["json3", "vtt"]

    for sub_format in formats:
        for lang in sub_langs:
            out_tmpl = f"{base_tmpl}.{lang}"
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": [lang],
                "subtitlesformat": sub_format,
                "skip_download": True,
                "outtmpl": out_tmpl,
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])
            except Exception as e:
                logger.debug("yt-dlp caption fetch (lang=%s fmt=%s) failed: %s", lang, sub_format, e)
                continue

            # yt-dlp names auto-subs "<id>.<lang>.<fmt>" and manual subs "<id>.<lang>.<fmt>"
            candidate = temp_dir / f"{video_id}.{lang}.{sub_format}"
            if candidate.exists() and candidate.stat().st_size > 0:
                logger.info(
                    "YouTube caption track found: lang=%s format=%s file=%s",
                    lang, sub_format, candidate.name,
                )
                return candidate

    # Last resort: list everything we actually downloaded for this id.
    for ext in ("json3", "vtt", "srv3", "srt", "ttml"):
        for f in sorted(temp_dir.glob(f"{video_id}.*.{ext}")):
            if f.stat().st_size > 0:
                logger.info("YouTube caption fallback track: %s", f.name)
                return f
    return None


def _parse_json3_captions(raw: str) -> Optional[Dict[str, Any]]:
    """Parse YouTube json3 caption format into a Whisper-like result dict.

    json3 events carry ``tStartMs`` (cue start, ms) and ``dDurationMs``
    (cue duration, ms); word-level offsets live in ``segs[].tOffsetMs``
    (ms relative to the cue start). Returns ``None`` if no usable cues.

    The returned dict mirrors a Whisper result: ``segments`` with
    ``start``/``end`` in seconds and ``words`` with ``word``/``start``/``end``
    in seconds, plus a top-level ``text`` and ``language``.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    events = data.get("events") or []
    if not events:
        return None

    segments: List[Dict[str, Any]] = []
    all_words: List[Dict[str, Any]] = []
    full_text_parts: List[str] = []

    for event in events:
        segs = event.get("segs") or []
        # Skip pure layout/newline events with no text.
        text_parts = [s.get("utf8", "") for s in segs if s.get("utf8")]
        cue_text = "".join(text_parts).strip()
        if not cue_text:
            continue

        start_ms = int(event.get("tStartMs", 0))
        duration_ms = int(event.get("dDurationMs", 0) or 0)
        end_ms = start_ms + duration_ms if duration_ms > 0 else start_ms

        # Word-level offsets: each seg with tOffsetMs is a word boundary.
        words: List[Dict[str, Any]] = []
        word_tokens = []
        for seg in segs:
            piece = (seg.get("utf8") or "").strip()
            if not piece:
                continue
            offset_ms = start_ms + int(seg.get("tOffsetMs", 0) or 0)
            # A seg may contain several whitespace-separated tokens; split so
            # each word gets a proportional sub-offset (matches Whisper shape).
            tokens = piece.split()
            if not tokens:
                continue
            for i, tok in enumerate(tokens):
                # Spread the seg across its tokens evenly when multiple exist.
                tok_offset = offset_ms + (i * max(1, duration_ms // max(1, len(tokens))) if duration_ms else 0)
                word_tokens.append((tok, tok_offset))

        if word_tokens:
            # Assign each word an end = next word start (or cue end).
            for idx, (tok, w_start) in enumerate(word_tokens):
                w_end = word_tokens[idx + 1][1] if idx + 1 < len(word_tokens) else end_ms
                words.append({
                    "word": tok,
                    "start": w_start / 1000.0,
                    "end": w_end / 1000.0,
                    "probability": 1.0,
                })
        else:
            # No per-word offsets: synthesize one word spanning the cue.
            words.append({
                "word": cue_text,
                "start": start_ms / 1000.0,
                "end": end_ms / 1000.0,
                "probability": 1.0,
            })

        segment = {
            "id": len(segments),
            "start": start_ms / 1000.0,
            "end": end_ms / 1000.0,
            "text": cue_text,
            "words": words,
        }
        segments.append(segment)
        all_words.extend(words)
        full_text_parts.append(cue_text)

    if not segments:
        return None

    return {
        "text": " ".join(full_text_parts),
        "segments": segments,
        "words": all_words,
        "language": None,
    }


_VTT_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{1,2}:\d{2}\.\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{1,2}:\d{2}\.\d{3})"
)


def _vtt_timestamp_to_seconds(ts: str) -> float:
    parts = ts.split(":")
    seconds = float(parts[-1])
    if len(parts) == 3:
        seconds += int(parts[0]) * 3600 + int(parts[1]) * 60
    elif len(parts) == 2:
        seconds += int(parts[0]) * 60
    return seconds


def _parse_vtt_captions(raw: str) -> Optional[Dict[str, Any]]:
    """Parse WebVTT caption format into a Whisper-like result dict.

    VTT cues only carry cue-level timing (no per-word offsets), so each cue
    becomes one segment and its words get evenly distributed across the cue
    duration. Returns ``None`` if no timed cues are found.
    """
    segments: List[Dict[str, Any]] = []
    all_words: List[Dict[str, Any]] = []
    full_text_parts: List[str] = []

    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        match = _VTT_TIMESTAMP_RE.match(line)
        if not match:
            i += 1
            continue
        start_s = _vtt_timestamp_to_seconds(match.group(1))
        end_s = _vtt_timestamp_to_seconds(match.group(2))
        i += 1
        cue_lines: List[str] = []
        while i < len(lines) and lines[i].strip():
            stripped = lines[i].strip()
            if "-->" in stripped:
                break
            cue_lines.append(stripped)
            i += 1
        cue_text = " ".join(cue_lines).strip()
        if not cue_text or end_s <= start_s:
            continue

        tokens = cue_text.split()
        if not tokens:
            continue
        dur = end_s - start_s
        per = dur / len(tokens)
        words = []
        for idx, tok in enumerate(tokens):
            words.append({
                "word": tok,
                "start": start_s + idx * per,
                "end": start_s + (idx + 1) * per,
                "probability": 1.0,
            })
        segments.append({
            "id": len(segments),
            "start": start_s,
            "end": end_s,
            "text": cue_text,
            "words": words,
        })
        all_words.extend(words)
        full_text_parts.append(cue_text)

    if not segments:
        return None
    return {
        "text": " ".join(full_text_parts),
        "segments": segments,
        "words": all_words,
        "language": None,
    }


def transcribe_with_youtube_captions(video_url: str) -> Optional[Dict[str, Any]]:
    """Extract a timed transcript from a YouTube video's captions via yt-dlp.

    Only valid for YouTube-sourced videos. Returns a Whisper-shaped result dict
    (segments + word-level timestamps in seconds) when captions are available,
    or ``None`` when the video has no usable caption track.

    Unlike the older plain-text implementation, this preserves cue timings and
    per-word offsets (when present in json3), so downstream subtitle generation
    and clip-bound grounding work the same as a Whisper transcript.
    """
    video_id = None
    match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", video_url)
    if match:
        video_id = match.group(1)
    if not video_id:
        logger.error("Could not extract YouTube video ID from URL: %s", video_url)
        return None

    temp_dir = Path(get_config().temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        subs_path = _download_youtube_captions(video_url, video_id, temp_dir)
        if not subs_path or not subs_path.exists():
            logger.warning("No captions found for YouTube video %s", video_id)
            return None

        raw = subs_path.read_text(encoding="utf-8", errors="replace")
        if subs_path.suffix == ".json3":
            result = _parse_json3_captions(raw)
        else:
            result = _parse_vtt_captions(raw)

        if not result:
            logger.warning("YouTube captions for %s were empty or unparseable", video_id)
            return None

        logger.info(
            "YouTube caption transcript: %d segments, %d words, %d chars",
            len(result.get("segments", [])),
            len(result.get("words", [])),
            len(result.get("text", "")),
        )
        return result

    except Exception as e:
        logger.error("Failed to extract YouTube captions: %s", e)
        return None
    finally:
        for f in temp_dir.glob(f"{video_id}.*"):
            if f.suffix in (".vtt", ".srt", ".ttml", ".json3", ".json", ".srv3"):
                try:
                    f.unlink()
                except OSError:
                    pass


def get_video_transcript(
    video_path: Path,
    speech_model: str = "universal",
    source_url: Optional[str] = None,
) -> str:
    """Get a video transcript using the configured provider.

    Dispatches to AssemblyAI, local Whisper, or YouTube captions based on
    ``TRANSCRIPTION_PROVIDER``. ``source_url`` enables the youtube_captions
    provider, which needs the original URL rather than a local file path.

    When the source is a YouTube URL and YouTube captions are available, they
    are used regardless of the configured provider (they are free, instant, and
    often include word-level timing). If captions are missing or unparseable,
    the configured provider (Whisper/AssemblyAI) is used as a fallback. Set
    ``YOUTUBE_CAPTIONS_FIRST=false`` to disable this shortcut.
    """
    logger.info(f"Getting transcript for: {video_path}")
    runtime_config = get_config()
    provider = runtime_config.transcription_provider

    youtube_first = _youtube_captions_first_enabled(runtime_config)
    if youtube_first and source_url and _is_youtube_url(source_url):
        caption_result = transcribe_with_youtube_captions(source_url)
        if caption_result:
            return _format_youtube_captions(video_path, caption_result, runtime_config)
        logger.info(
            "YouTube captions unavailable for %s; falling back to %s",
            source_url, provider,
        )

    if provider == "whisper":
        return _get_transcript_with_whisper(video_path, runtime_config)
    if provider == "youtube_captions":
        if not source_url:
            raise ValueError(
                "youtube_captions provider requires a YouTube URL. "
                "Pass source_url to get_video_transcript()."
            )
        caption_result = transcribe_with_youtube_captions(source_url)
        if not caption_result:
            raise RuntimeError(
                "YouTube caption extraction failed or returned no captions. "
                "Falling back requires a different TRANSCRIPTION_PROVIDER."
            )
        return _format_youtube_captions(video_path, caption_result, runtime_config)
    return _get_transcript_with_assemblyai(video_path, speech_model, runtime_config)


def _youtube_captions_first_enabled(runtime_config) -> bool:
    """Whether YouTube captions should preempt the configured provider."""
    return getattr(runtime_config, "youtube_captions_first", True)


def _is_youtube_url(url: str) -> bool:
    return bool(url and ("youtube.com/" in url or "youtu.be/" in url))


def _format_youtube_captions(
    video_path: Path, caption_result: Dict[str, Any], runtime_config
) -> str:
    """Cache and format a YouTube caption result for downstream pipeline use.

    Mirrors the Whisper path: cache the word-level sidecar, then format the
    transcript into timestamped lines for AI analysis.
    """
    cache_transcript_data(video_path, caption_result)
    formatted_lines = format_transcript_for_analysis(caption_result)
    result = "\n".join(formatted_lines)
    logger.info(
        "YouTube caption transcript formatted: %d segments, %d chars",
        len(formatted_lines),
        len(result),
    )
    return result


def _get_transcript_with_assemblyai(
    video_path: Path, speech_model: str, runtime_config
) -> str:
    """Get transcript using AssemblyAI with word-level timing for precise subtitles."""
    # A job owns its client: concurrent jobs must not mutate shared SDK settings.
    client = aai.Client(settings=aai.Settings(
        api_key=runtime_config.assembly_ai_api_key,
        base_url=aai.settings.base_url,
        http_timeout=getattr(runtime_config, "assembly_ai_request_timeout_seconds", 60),
    ))
    transcriber = aai.Transcriber(client=client)

    # AssemblyAI now requires the plural `speech_models` list; the singular
    # `best`/`nano`/`universal` values were deprecated server-side.
    selected_model = getattr(runtime_config, "assembly_ai_speech_model", "") or speech_model
    speech_models_value = _assemblyai_speech_models_value(selected_model)

    # Without a language, AssemblyAI assumes US English and garbles everything
    # else; detection also routes to the first listed model that supports it.
    language = runtime_config.transcription_language
    config_obj = aai.TranscriptionConfig(
        speaker_labels=True,
        punctuate=True,
        format_text=True,
        speech_models=speech_models_value,
        language_code=language or None,
        language_detection=None if language else True,
    )

    try:
        logger.info("Starting AssemblyAI transcription")
        transcription_media_path = _prepare_audio_for_transcription(video_path)
        # Once submitted, a polling timeout must not submit another paid job or
        # restart the full transcription budget. Retry status GETs above instead.
        transcript = _submit_and_wait_for_assemblyai_transcript(
            transcriber,
            transcription_media_path,
            config_obj,
            runtime_config.assembly_ai_http_timeout_seconds,
        )

        if transcript is None:
            raise RuntimeError("AssemblyAI transcription did not return a transcript")

        if transcript.status == aai.TranscriptStatus.error:
            logger.error(f"AssemblyAI transcription failed: {transcript.error}")
            raise Exception(f"Transcription failed: {transcript.error}")

        response = getattr(transcript, "json_response", None) or {}
        logger.info(
            "AssemblyAI transcript language: %s (confidence: %s)",
            response.get("language_code") or language or "unknown",
            response.get("language_confidence"),
        )

        formatted_lines = format_transcript_for_analysis(transcript)
        cache_transcript_data(video_path, transcript)

        result = "\n".join(formatted_lines)
        logger.info(
            f"Transcript formatted: {len(formatted_lines)} segments, {len(result)} chars"
        )
        return result

    except Exception as e:
        logger.error(f"Error in AssemblyAI transcription: {e}")
        raise
    finally:
        client.http_client.close()


def _get_transcript_with_whisper(video_path: Path, runtime_config) -> str:
    """Get transcript using local Whisper with word-level timestamps."""
    model_name = runtime_config.whisper_model
    logger.info("Starting Whisper transcription with model: %s", model_name)
    whisper_result = transcribe_with_whisper(
        video_path, model_name, runtime_config.transcription_language or None
    )

    formatted_lines = format_transcript_for_analysis(whisper_result)
    cache_transcript_data(video_path, whisper_result)

    result = "\n".join(formatted_lines)
    logger.info(
        "Whisper transcript formatted: %d segments, %d chars",
        len(formatted_lines),
        len(result),
    )
    return result


def cache_transcript_data(video_path: Path, transcript) -> None:
    """Cache transcript data for subtitle generation.

    Handles both AssemblyAI transcript objects and Whisper result dicts.
    """
    cache_path = video_path.with_suffix(".transcript_cache.json")

    if isinstance(transcript, dict):
        cache_data = _whisper_result_to_transcript_data(transcript)
        with open(cache_path, "w") as f:
            json.dump(cache_data, f)
        logger.info("Cached %d words to %s", len(cache_data["words"]), cache_path)
        return

    words_data = []
    if transcript.words:
        words_data = [_serialize_transcript_word(word) for word in transcript.words]

    utterances_data = []
    if getattr(transcript, "utterances", None):
        utterances_data = [
            {
                "text": utterance.text,
                "start": utterance.start,
                "end": utterance.end,
                "speaker": getattr(utterance, "speaker", None),
                "words": [
                    _serialize_transcript_word(word)
                    for word in getattr(utterance, "words", []) or []
                ],
            }
            for utterance in transcript.utterances
        ]

    response = getattr(transcript, "json_response", None)
    language_code = response.get("language_code") if isinstance(response, dict) else None
    if not language_code:
        language_code = getattr(getattr(transcript, "config", None), "language_code", None)

    cache_data = {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words_data,
        "utterances": utterances_data,
        "text": transcript.text,
        # Lets language-specific cleanup (e.g. default filler words) avoid
        # cutting ordinary words in other languages.
        "language_code": language_code if isinstance(language_code, str) else None,
    }

    with open(cache_path, "w") as f:
        json.dump(cache_data, f)

    logger.info(f"Cached {len(words_data)} words to {cache_path}")


def load_cached_transcript_data(video_path: Path) -> Optional[Dict]:
    """Load cached AssemblyAI transcript data."""
    cache_path = video_path.with_suffix(".transcript_cache.json")

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "r") as f:
            payload = json.load(f)
            if "version" not in payload:
                payload["version"] = TRANSCRIPT_CACHE_SCHEMA_VERSION
                payload.setdefault("utterances", [])
            return payload
    except Exception as e:
        logger.warning(f"Failed to load transcript cache: {e}")
        return None


def _serialize_transcript_word(word) -> Dict[str, Any]:
    if isinstance(word, dict):
        return {
            "text": word.get("word", word.get("text", "")),
            "start": int(word["start"] * 1000) if isinstance(word.get("start"), float) else int(word.get("start", 0)),
            "end": int(word["end"] * 1000) if isinstance(word.get("end"), float) else int(word.get("end", 0)),
            "confidence": word.get("probability", word.get("confidence", 1.0)),
            "speaker": word.get("speaker"),
        }
    return {
        "text": word.text,
        "start": word.start,
        "end": word.end,
        "confidence": word.confidence if hasattr(word, "confidence") else 1.0,
        "speaker": getattr(word, "speaker", None),
    }


def _join_transcript_tokens(tokens: List[str]) -> str:
    text = " ".join(token.strip() for token in tokens if token and token.strip())
    for before, after in (
        (" ,", ","),
        (" .", "."),
        (" !", "!"),
        (" ?", "?"),
        (" ;", ";"),
        (" :", ":"),
        (" n't", "n't"),
        (" 're", "'re"),
        (" 've", "'ve"),
        (" 'll", "'ll"),
        (" 'd", "'d"),
        (" 'm", "'m"),
        (" 's", "'s"),
    ):
        text = text.replace(before, after)
    return text.strip()


def _format_words_for_analysis(
    words: List[Any],
    speaker: Optional[str] = None,
    *,
    min_words_per_segment: int = ANALYSIS_SEGMENT_MIN_WORDS,
    max_words_per_segment: int = ANALYSIS_SEGMENT_MAX_WORDS,
    max_duration_ms: int = ANALYSIS_SEGMENT_MAX_DURATION_MS,
) -> List[str]:
    if not words:
        return []

    formatted_lines: List[str] = []
    current_words: List[Any] = []
    current_start: Optional[int] = None

    def flush_segment() -> None:
        nonlocal current_words, current_start
        if not current_words:
            return
        start_time = format_ms_to_timestamp(current_words[0].start)
        end_time = format_ms_to_timestamp(current_words[-1].end)
        text = _join_transcript_tokens([word.text for word in current_words])
        if text:
            speaker_prefix = f"Speaker {speaker}: " if speaker else ""
            formatted_lines.append(f"[{start_time} - {end_time}] {speaker_prefix}{text}")
        current_words = []
        current_start = None

    for word in words:
        if current_start is None:
            current_start = word.start

        current_words.append(word)
        duration_ms = word.end - current_start
        segment_word_count = len(current_words)
        ends_sentence = str(word.text).endswith((".", "!", "?"))

        should_flush = False
        if segment_word_count >= max_words_per_segment:
            should_flush = True
        elif (
            ends_sentence
            and segment_word_count >= min_words_per_segment
        ):
            should_flush = True
        elif (
            duration_ms >= max_duration_ms
            and segment_word_count >= min_words_per_segment
        ):
            should_flush = True

        if should_flush:
            flush_segment()

    flush_segment()
    return formatted_lines


def format_transcript_for_analysis(transcript) -> List[str]:
    """Format transcripts into readable timestamped segments for AI analysis.

    Handles both AssemblyAI transcript objects (utterances/words with ms
    timings) and Whisper result dicts (segments with second-based timings).
    """
    # Whisper result dict: treat each segment as an utterance, converting the
    # second-based timings to milliseconds to match the timestamp formatter.
    if isinstance(transcript, dict):
        formatted_lines = []
        for segment in transcript.get("segments") or []:
            start_ms = int(segment.get("start", 0) * 1000)
            end_ms = int(segment.get("end", 0) * 1000)
            formatted_lines.append(
                f"[{format_ms_to_timestamp(start_ms)} - {format_ms_to_timestamp(end_ms)}] "
                f"{segment.get('text', '').strip()}"
            )
        return formatted_lines

    utterances = getattr(transcript, "utterances", None) or []
    if utterances:
        formatted_lines = []
        for utterance in utterances:
            utterance_words = list(getattr(utterance, "words", []) or [])
            utterance_duration = max(0, int(utterance.end) - int(utterance.start))
            if utterance_words and (
                utterance_duration > ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS
                or len(utterance_words) > ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS
            ):
                formatted_lines.extend(
                    _format_words_for_analysis(
                        utterance_words,
                        getattr(utterance, "speaker", None),
                        max_words_per_segment=ANALYSIS_LONG_UTTERANCE_MAX_WORDS,
                        max_duration_ms=ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS,
                    )
                )
                continue

            start_time = format_ms_to_timestamp(utterance.start)
            end_time = format_ms_to_timestamp(utterance.end)
            speaker = getattr(utterance, "speaker", None)
            speaker_prefix = f"Speaker {speaker}: " if speaker else ""
            formatted_lines.append(
                f"[{start_time} - {end_time}] {speaker_prefix}{utterance.text}"
            )
        return formatted_lines

    formatted_lines = []
    words = getattr(transcript, "words", None) or []
    if not words:
        return formatted_lines

    logger.info(f"Processing {len(words)} words with precise timing")
    return _format_words_for_analysis(words)


def format_ms_to_timestamp(ms: int) -> str:
    """Format milliseconds to MM:SS format."""
    seconds = ms // 1000
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:02d}"

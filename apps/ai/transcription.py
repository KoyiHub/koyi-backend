"""Speech to text, behind the same kind of boundary as the model.

Audio matters more here than in most products: at Levels 1 and 2 a child who
cannot read cannot type, so spoken answers are the evidence exactly where
placement is hardest to get right.

That makes transcription the weakest link in the marking path, and it is worth
being honest about why. Whisper on Nigerian-accented child speech reading
English is not a solved problem. Two things follow, and both are built in
rather than left to good intentions: the transcript is stored beside the audio
so a teacher can check it, and a low-confidence transcript routes the response
to review instead of quietly marking a child wrong.
"""

import time
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings


class TranscriptionError(Exception):
    """The audio could not be transcribed."""


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    provider: str
    model_id: str
    #: 0-1 where the provider reports one. None means it did not.
    confidence: float | None = None
    latency_ms: int = 0


class TranscriptionClient(Protocol):
    name: str

    def transcribe(self, *, audio_url: str) -> Transcript: ...


class WhisperHTTPClient:
    """A self-hosted Whisper endpoint.

    Deliberately spoken to over plain HTTP rather than through a library, so
    the same adapter serves whisper.cpp, faster-whisper or anything else that
    accepts audio and returns text. Which one is running is deployment's
    business, not this module's.
    """

    name = "whisper"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.WHISPER_BASE_URL).rstrip("/")
        self.model = model or settings.WHISPER_MODEL
        self.timeout = timeout or settings.AI_TIMEOUT_SECONDS

    def transcribe(self, *, audio_url: str) -> Transcript:
        import httpx

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                audio = client.get(audio_url)
                audio.raise_for_status()
                response = client.post(
                    f"{self.base_url}/v1/audio/transcriptions",
                    files={"file": ("response.wav", audio.content)},
                    data={"model": self.model, "response_format": "verbose_json"},
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            raise TranscriptionError(f"whisper failed for {audio_url}: {exc}") from exc

        return Transcript(
            text=(body.get("text") or "").strip(),
            provider=self.name,
            model_id=self.model,
            confidence=_confidence(body),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


class ScriptedTranscriber:
    """Canned transcripts, for tests and for running with no ASR service."""

    name = "scripted"

    def __init__(self, text: str = "", confidence: float | None = 0.9) -> None:
        self.text = text
        self.confidence = confidence
        self.calls: list[str] = []

    def transcribe(self, *, audio_url: str) -> Transcript:
        self.calls.append(audio_url)
        return Transcript(
            text=self.text,
            provider=self.name,
            model_id="scripted",
            confidence=self.confidence,
        )


_registry: dict = {}


def register_transcriber(client) -> None:
    _registry["client"] = client


def reset_transcriber() -> None:
    """Forget any installed transcriber. See `reset_client`."""
    _registry.pop("client", None)


def get_transcriber() -> TranscriptionClient:
    if "client" in _registry:
        return _registry["client"]
    if not getattr(settings, "WHISPER_BASE_URL", ""):
        return ScriptedTranscriber()
    return WhisperHTTPClient()


def _confidence(body: dict) -> float | None:
    """Whisper reports per-segment log-probability, not a confidence.

    Averaging those and exponentiating gives something on 0-1 that behaves
    like one. It is a proxy - good enough to route the worst transcripts to a
    teacher, not good enough to mean anything on its own.
    """
    import math

    segments = body.get("segments") or []
    logprobs = [s["avg_logprob"] for s in segments if "avg_logprob" in s]
    if not logprobs:
        return None
    return round(math.exp(sum(logprobs) / len(logprobs)), 3)


__all__ = [
    "ScriptedTranscriber",
    "Transcript",
    "TranscriptionClient",
    "TranscriptionError",
    "WhisperHTTPClient",
    "get_transcriber",
    "register_transcriber",
    "reset_transcriber",
]

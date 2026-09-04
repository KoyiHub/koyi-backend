"""The three jobs the assessment loop needs.

Each one does the same four things: build a prompt, call the provider under a
schema, validate the reply against the domain, and record the call. The
recording is not optional - a failed generation is as informative as a
successful one, because a run of invalid output is how a drifting prompt or a
swapped model announces itself.

Nothing here decides anything a rule could decide. Marking settles one item;
placement is computed from marked items by `apps.assessments.placement`, which
never calls a model.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from apps.ai import prompts
from apps.ai.client import LLMError, get_client
from apps.ai.enums import GenerationStatus, JobType
from apps.ai.models import AIGeneration
from apps.ai.schemas import (
    MARKING_SCHEMA,
    NARRATIVE_SCHEMA,
    TAGGING_SCHEMA,
    MarkingVerdict,
    Narrative,
    SchemaError,
    TagSuggestion,
    parse_marking,
    parse_narrative,
    parse_tags,
)
from apps.ai.transcription import TranscriptionError, get_transcriber

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobOutcome[T]:
    """What a job produced, and the row that records it.

    Generic so a caller gets the type it asked for rather than `object` - a
    marking job hands back a verdict, and the compiler should know it.
    """

    value: T | None
    generation: AIGeneration

    @property
    def ok(self) -> bool:
        return self.value is not None


def _run[T](
    *,
    job: JobType,
    payload: str,
    schema: dict,
    parse: Callable[[dict], T],
    subject_type: str = "",
    subject_id: str = "",
) -> JobOutcome[T]:
    """One call, recorded whatever happens."""
    bundle = prompts.build(job, payload=payload)
    client = get_client(job)

    record = {
        "job_type": job,
        "subject_type": subject_type,
        "subject_id": str(subject_id),
        "prompt_version": bundle.prompt_version,
        "input_hash": bundle.fingerprint(),
        "provider": getattr(client, "name", "unknown"),
        "model_id": "",
    }

    try:
        result = client.complete(prompt=bundle, schema=schema, job=str(job))
    except LLMError as exc:
        logger.warning("ai job failed", extra={"job": str(job), "error": str(exc)})
        return JobOutcome(
            None,
            AIGeneration.objects.create(
                **record, status=GenerationStatus.FAILED, error=str(exc)[:2000]
            ),
        )

    record["model_id"] = result.model_id
    try:
        value = parse(result.parsed)
    except SchemaError as exc:
        # The reply satisfied the schema but not the domain - an invented
        # subskill, a level outside its range. Kept, because this is the
        # signal that guidance or the model has drifted.
        logger.warning("ai job returned invalid output", extra={"job": str(job), "error": str(exc)})
        return JobOutcome(
            None,
            AIGeneration.objects.create(
                **record,
                status=GenerationStatus.INVALID,
                raw_output=result.raw[:8000],
                error=str(exc)[:2000],
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
            ),
        )

    return JobOutcome(
        value,
        AIGeneration.objects.create(
            **record,
            status=GenerationStatus.SUCCEEDED,
            raw_output=result.raw[:8000],
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
        ),
    )


# ---------------------------------------------------------------------------
# Marking
# ---------------------------------------------------------------------------


def mark_written_response(response) -> JobOutcome[MarkingVerdict]:
    """Mark one written answer."""
    question = response.assessment_question
    payload = _marking_payload(
        question=question,
        given=response.text_value,
        label="What the child wrote",
    )
    return _run(
        job=JobType.MARK_TEXT_RESPONSE,
        payload=payload,
        schema=MARKING_SCHEMA,
        parse=parse_marking,
        subject_type="assessment_question_response",
        subject_id=response.pk,
    )


def mark_spoken_response(response, *, transcript: str) -> JobOutcome[MarkingVerdict]:
    """Mark one spoken answer from its transcript."""
    question = response.assessment_question
    payload = _marking_payload(
        question=question,
        given=transcript,
        label="Transcript of what the child said",
    )
    return _run(
        job=JobType.MARK_AUDIO_RESPONSE,
        payload=payload,
        schema=MARKING_SCHEMA,
        parse=parse_marking,
        subject_type="assessment_question_response",
        subject_id=response.pk,
    )


def _marking_payload(*, question, given: str, label: str) -> str:
    answer = getattr(question, "answer", None)
    expected = (answer.value if answer else "") or "(none recorded)"
    return "\n".join(
        [
            f"Question: {question.text}",
            f"Subskill: {question.subskill.name}",
            f"FLN level: {question.fln_level}",
            f"Expected answer: {expected}",
            f"{label}: {given or '(nothing)'}",
        ]
    )


def transcribe_response(response) -> str | None:
    """Turn a spoken answer into text, or return None if it could not be.

    Failure is not the child's fault, so it must not become their mark. The
    caller leaves the response pending for a teacher instead.
    """
    media = response.media_value
    if media is None or not media.url:
        return None
    try:
        transcript = get_transcriber().transcribe(audio_url=media.url)
    except TranscriptionError as exc:
        logger.warning(
            "transcription failed", extra={"response_id": str(response.pk), "error": str(exc)}
        )
        return None
    return transcript.text


# ---------------------------------------------------------------------------
# Tag suggestion
# ---------------------------------------------------------------------------


def suggest_question_tags(*, text: str, options: list[str], subskills) -> JobOutcome[TagSuggestion]:
    """Propose a subskill and level for an authored question.

    `subskills` is what the model may choose from, listed with the level range
    each is assessed across. Giving the ranges up front means most invalid
    suggestions are never generated; the parser rejects the rest.
    """
    known = {s.code: s for s in subskills}
    catalogue = "\n".join(
        f"- {s.code}: {s.name} ({s.skill.name}, {s.skill.domain}, "
        f"levels {s.level_range[0]}-{s.level_range[1]})"
        for s in subskills
    )
    payload = "\n".join(
        [
            "Question the teacher wrote:",
            text,
            "",
            "Answer options:" if options else "No answer options.",
            *(f"- {option}" for option in options),
            "",
            "Choose exactly one subskill from this list:",
            catalogue,
        ]
    )
    return _run(
        job=JobType.SUGGEST_QUESTION_TAGS,
        payload=payload,
        schema=TAGGING_SCHEMA,
        parse=lambda data: parse_tags(data, known_subskills=known),
        subject_type="draft_question",
    )


# ---------------------------------------------------------------------------
# Narratives
# ---------------------------------------------------------------------------


def explain_assessment(analytics) -> JobOutcome[Narrative]:
    """Put prose over a class result.

    The figures are already computed and are passed in as text. The model is
    explaining them, which is the only shape of AI work this product does on
    the diagnosis side - nothing here changes a number.
    """
    return _run(
        job=JobType.ASSESSMENT_ANALYTICS,
        payload=_analytics_payload(analytics),
        schema=NARRATIVE_SCHEMA,
        parse=parse_narrative,
        subject_type="assessment",
        subject_id=analytics.assessment_id,
    )


def explain_student(breakdown) -> JobOutcome[Narrative]:
    """Put prose over one child's result."""
    return _run(
        job=JobType.STUDENT_SKILL_NARRATIVE,
        payload=_student_payload(breakdown),
        schema=NARRATIVE_SCHEMA,
        parse=parse_narrative,
        subject_type="student",
        subject_id=breakdown.student_id,
    )


def _analytics_payload(a) -> str:
    lines = [
        f"Assessment: {a.name}",
        f"Marking: {a.marking_status.marked} of {a.marking_status.total} answers marked, "
        f"{a.marking_status.pending} still pending.",
        f"Participation: {a.participation['submitted']} of {a.participation['assigned']} "
        "children submitted.",
        "",
        "Level distribution (children per level):",
    ]
    for domain, levels in a.level_distribution.items():
        spread = ", ".join(f"L{level}: {count}" for level, count in levels.items())
        lines.append(f"  {domain}: {spread}")

    if a.skill_matrix:
        lines += ["", "Pass rates by skill and level:"]
        lines += [
            f"  {cell.skill_name} ({cell.domain}) L{cell.fln_level}: {cell.passed}/{cell.total}"
            for cell in a.skill_matrix
        ]
    if a.most_missed:
        lines += ["", "Most missed subskills:"]
        lines += [
            f"  {m.subskill_name} (L{m.fln_level}, {m.skill_name}): {m.failed_pct}% did not pass"
            for m in a.most_missed
        ]
    return "\n".join(lines)


def _student_payload(b) -> str:
    lines = [
        f"Child: {b.full_name}",
        f"Literacy level: {b.literacy_level if b.literacy_level is not None else 'not assessed'}",
        f"Numeracy level: {b.numeracy_level if b.numeracy_level is not None else 'not assessed'}",
    ]
    if b.movement:
        lines += ["", "Movement since the last assessment:"]
        lines += [f"  {m.domain}: {m.previous} to {m.current} ({m.direction})" for m in b.movement]
    if b.skills:
        lines += ["", "By skill:"]
        for skill in b.skills:
            passed = skill.highest_level_passed
            broke = skill.broke_down_at
            lines.append(
                f"  {skill.skill_name} ({skill.domain}): "
                f"highest level passed {passed if passed is not None else 'none'}, "
                f"broke down at {broke if broke is not None else 'not reached'}"
            )
            if skill.weak_subskills:
                lines.append("    did not pass: " + ", ".join(skill.weak_subskills))
    return "\n".join(lines)


def apply_marking(response, verdict: MarkingVerdict, *, transcript: str = "") -> None:
    """Write a verdict onto a response.

    A low-confidence verdict is stored but leaves `is_correct` null, so the
    matrix skips it and a teacher sees it in the review queue. Pending is not
    wrong, and a confident-looking mistake costs a child more than a flagged
    uncertainty costs a teacher.
    """
    from apps.assessments.enums import GradedBy

    fields = ["graded_by", "grading_confidence", "error_type", "observation_note", "updated_at"]
    response.graded_by = GradedBy.AI
    response.grading_confidence = verdict.confidence
    response.error_type = verdict.error_type
    response.observation_note = verdict.observation_note
    if transcript:
        response.transcript = transcript
        fields.append("transcript")

    if not verdict.needs_review:
        response.is_correct = verdict.is_correct
        response.awarded_points = response.assessment_question.point if verdict.is_correct else 0
        fields += ["is_correct", "awarded_points"]

    response.save(update_fields=fields)


__all__ = [
    "JobOutcome",
    "MarkingVerdict",
    "Narrative",
    "TagSuggestion",
    "apply_marking",
    "explain_assessment",
    "explain_student",
    "mark_spoken_response",
    "mark_written_response",
    "suggest_question_tags",
    "transcribe_response",
]

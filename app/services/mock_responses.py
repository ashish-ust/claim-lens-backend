"""Canned Claude replies for MOCK_MODE=true, so the UI can be built and demoed without an API key.

Demo control: put a tag in the narrative to choose the outcome of the Claude-based checks:
  mock:edited        -> photo check says POSSIBLY_EDITED
  mock:ai            -> photo check says LIKELY_AI_GENERATED
  mock:partial       -> consistency says PARTIALLY_CONSISTENT
  mock:inconsistent  -> consistency says INCONSISTENT
Without a tag the outcome is LIKELY_AUTHENTIC / CONSISTENT.
"""

from __future__ import annotations

from app.schemas import AIImageLLM, BriefLLM, ConsistencyLLM


def _has(narrative: str, tag: str) -> bool:
    return tag in narrative.lower()


def mock_ai_image(narrative: str, exif_findings: list[str]) -> AIImageLLM:
    """Canned per-image photo verdict."""
    if _has(narrative, "mock:ai"):
        return AIImageLLM(
            verdict="LIKELY_AI_GENERATED",
            confidence=0.72,
            indicators=[
                "Background text on a sign is smeared and partly unreadable",
                "Reflections on the bumper do not match the surrounding scene",
            ],
            explanation="Several visual cues are consistent with synthetic generation. "
            "This is a probabilistic assessment and is not proof.",
        )
    if _has(narrative, "mock:edited"):
        return AIImageLLM(
            verdict="POSSIBLY_EDITED",
            confidence=0.61,
            indicators=["Edge halo around the damaged panel", "Lighting on the wheel arch looks inconsistent"],
            explanation="Some local regions look inconsistent with the rest of the frame. "
            "This may be compression or retouching; it is not conclusive.",
        )
    return AIImageLLM(
        verdict="LIKELY_AUTHENTIC",
        confidence=0.8,
        indicators=[],
        explanation="No obvious visual signs of generation or manipulation were seen. "
        "This is a probabilistic assessment, not a guarantee.",
    )


def mock_consistency(narrative: str) -> ConsistencyLLM:
    """Canned narrative-vs-photo comparison."""
    if _has(narrative, "mock:inconsistent"):
        return ConsistencyLLM(
            verdict="INCONSISTENT",
            confidence=0.7,
            summary="The visible damage location differs from the one described in the narrative.",
            matched_details=["A dark vehicle is visible"],
            contradictions=["Narrative says rear damage; photos show front damage"],
            missing_or_unverifiable=["Time of day", "Other party's vehicle"],
        )
    if _has(narrative, "mock:partial"):
        return ConsistencyLLM(
            verdict="PARTIALLY_CONSISTENT",
            confidence=0.65,
            summary="The vehicle and damage area match, but the setting is only partly visible.",
            matched_details=["Vehicle type matches", "Damage to the left side is visible"],
            contradictions=["Narrative mentions heavy rain; the photos look dry"],
            missing_or_unverifiable=["Exact speed", "Other party's vehicle"],
        )
    return ConsistencyLLM(
        verdict="CONSISTENT",
        confidence=0.85,
        summary="Visible vehicle and damage area are consistent with the narrative.",
        matched_details=["Vehicle type matches", "Damage location matches the narrative"],
        contradictions=[],
        missing_or_unverifiable=["Time of the incident", "Speed at impact"],
    )


def mock_brief(risk_level: str, duplicate_verdict: str, ai_verdict: str, consistency_verdict: str) -> BriefLLM:
    """Canned investigator brief built from the three detector verdicts."""
    return BriefLLM(
        brief=(
            f"Automated screening rates this claim {risk_level}. "
            f"Duplicate check: {duplicate_verdict}. Photo authenticity check: {ai_verdict}. "
            f"Narrative consistency: {consistency_verdict}. "
            "These are automated signals to guide review, not findings of fraud; "
            "please verify the details with the claimant and any independent sources."
        ),
        follow_up_questions=[
            "Can you describe where and when the incident happened, and the weather at the time?",
            "Do you have the original, unedited photos (for example, from your phone's camera roll)?",
            "Were any other vehicles or witnesses involved, and do you have their details?",
        ],
    )

"""Combine the three detector results into a deterministic risk score, then ask Claude for a brief.

The score, risk level and recommendation are plain rules. Claude only writes the prose
(brief and follow-up questions), grounded in the three findings. Recommendations are advisory.
"""

from __future__ import annotations

from app.config import get_settings
from app.schemas import BriefLLM, Detectors, Overall, Recommendation, RiskLevel
from app.services.claude_client import ClaudeError, ask_json, text_block
from app.services.mock_responses import mock_brief

MAX_SCORE = 100

DUPLICATE_POINTS = {"DUPLICATE": 45, "POSSIBLE_DUPLICATE": 25}
AI_POINTS = {"LIKELY_AI_GENERATED": 40, "POSSIBLY_EDITED": 20}
CONSISTENCY_POINTS = {"INCONSISTENT": 35, "PARTIALLY_CONSISTENT": 15}

RECOMMENDATION: dict[str, Recommendation] = {"LOW": "PROCEED", "MEDIUM": "REVIEW", "HIGH": "ESCALATE"}

BRIEF_SYSTEM = """You are writing a short note for an insurance claims investigator about an automated screening.
Use ONLY the three findings provided. Do not add facts, do not guess causes.
Rules:
- "brief": 3 to 5 sentences. Say what each check found, in plain language.
- Never state or imply that fraud is confirmed. Use hedged wording such as "signals", "consistent with", "may".
- "follow_up_questions": exactly 3 questions to ask the claimant, each tied to a finding above."""


def score_claim(detectors: Detectors) -> int:
    """Deterministic score in 0..100 from the three verdicts."""
    total = (
        DUPLICATE_POINTS.get(detectors.duplicate.verdict, 0)
        + AI_POINTS.get(detectors.ai_generated.verdict, 0)
        + CONSISTENCY_POINTS.get(detectors.consistency.verdict, 0)
    )
    return min(total, MAX_SCORE)


def risk_level_for(score: int) -> RiskLevel:
    """<25 LOW, 25-59 MEDIUM, >=60 HIGH."""
    if score >= 60:
        return "HIGH"
    if score >= 25:
        return "MEDIUM"
    return "LOW"


async def synthesize(detectors: Detectors) -> Overall:
    """Build the overall block. Falls back to a plain-text brief if Claude is unavailable."""
    score = score_claim(detectors)
    level = risk_level_for(score)
    recommendation = RECOMMENDATION[level]
    d_v = detectors.duplicate.verdict
    a_v = detectors.ai_generated.verdict
    c_v = detectors.consistency.verdict

    if get_settings().mock_mode:
        llm = mock_brief(level, d_v, a_v, c_v)
    else:
        payload = detectors.model_dump_json(indent=2)
        try:
            llm = await ask_json(
                BriefLLM,
                BRIEF_SYSTEM,
                [text_block(f"Overall automated risk level: {level}\n\nFindings (JSON):\n{payload}")],
                max_tokens=700,
            )
        except ClaudeError:
            llm = _fallback_brief(level, d_v, a_v, c_v)

    return Overall(
        risk_level=level,
        risk_score=score,
        recommendation=recommendation,
        brief=llm.brief,
        follow_up_questions=llm.follow_up_questions,
    )


def _fallback_brief(level: str, d_v: str, a_v: str, c_v: str) -> BriefLLM:
    """Deterministic brief used when the AI-written one cannot be produced."""
    return BriefLLM(
        brief=(
            f"Automated screening rates this claim {level}. Duplicate check: {d_v}. "
            f"Photo authenticity check: {a_v}. Narrative consistency: {c_v}. "
            "The AI-written summary was unavailable, so this text is generated from the rules only. "
            "These are signals to guide review, not findings of fraud."
        ),
        follow_up_questions=[
            "Can you describe where and when the incident happened?",
            "Do you have the original, unedited photos from your device?",
            "Were any other vehicles, drivers or witnesses involved?",
        ],
    )

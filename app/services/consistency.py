"""Check 3: does what the photos show match the claimant's narrative? One Claude call with all photos."""

from __future__ import annotations

from app.config import get_settings
from app.schemas import ConsistencyDetector, ConsistencyLLM
from app.services.claude_client import ClaudeError, ask_json, image_block, text_block
from app.services.image_utils import prepare_for_claude
from app.services.mock_responses import mock_consistency

SYSTEM = """You are assisting an insurance claims investigator. You will see one or two photos from a motor claim
and the claimant's written narrative. Judge whether what is VISIBLE matches the narrative:
vehicle type and count, damage location and severity, setting, weather, time of day.
Rules:
- Compare only what the photos can show. Do not penalise the narrative for details a photo cannot show
  (speed, intent, who was at fault, events before or after the photo). List those under missing_or_unverifiable.
- Your judgement is probabilistic. Never claim certainty and never state or imply fraud.
- Only list contradictions you can point to in the photos or narrative.
Verdicts: CONSISTENT, PARTIALLY_CONSISTENT, INCONSISTENT.
Return keys: verdict, confidence (0-1), summary, matched_details (array), contradictions (array),
missing_or_unverifiable (array)."""


async def check_consistency(images: list[bytes], narrative: str) -> ConsistencyDetector:
    """Compare the photos with the narrative. Returns UNAVAILABLE instead of raising on Claude failure."""
    if get_settings().mock_mode:
        llm = mock_consistency(narrative)
    else:
        content = []
        for i, data in enumerate(images, start=1):
            content.append(text_block(f"Photo {i}:"))
            content.append(image_block(prepare_for_claude(data)))
        content.append(text_block(f"Claimant narrative:\n\"\"\"\n{narrative}\n\"\"\""))
        try:
            llm = await ask_json(ConsistencyLLM, SYSTEM, content)
        except ClaudeError:
            return ConsistencyDetector(
                verdict="UNAVAILABLE",
                confidence=0.0,
                summary="The narrative consistency check could not be completed (AI service unavailable or invalid reply).",
            )

    return ConsistencyDetector(
        verdict=llm.verdict,
        confidence=llm.confidence,
        summary=llm.summary,
        matched_details=llm.matched_details,
        contradictions=llm.contradictions,
        missing_or_unverifiable=llm.missing_or_unverifiable,
    )

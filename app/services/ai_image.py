"""Check 2: AI-generated or edited photo detection.

Two signals per photo, both probabilistic:
  1. EXIF analysis with Pillow (deterministic). Missing EXIF is only a WEAK signal.
  2. A Claude vision call looking for visual signs of generation or manipulation.

Overall verdict = the most severe per-image verdict. Results are never presented as certain.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image

from app.config import get_settings
from app.schemas import AIGeneratedDetector, AIImageLLM, AIVerdict, PerImageAI
from app.services.claude_client import ClaudeError, ask_json, image_block, text_block
from app.services.image_utils import prepare_for_claude
from app.services.mock_responses import mock_ai_image

# EXIF tag ids
TAG_SOFTWARE = 0x0131
TAG_DATETIME = 0x0132
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
IFD_EXIF = 0x8769
TAG_DATETIME_ORIGINAL = 0x9003
EXIF_TIME_FORMAT = "%Y:%m:%d %H:%M:%S"

# Lower-case substrings matched against the EXIF Software tag
EDITING_TOOLS = ("photoshop", "gimp", "canva", "lightroom", "snapseed", "affinity", "paint.net", "pixelmator", "picsart")
AI_TOOLS = ("midjourney", "dall-e", "dalle", "stable diffusion", "stablediffusion", "firefly", "comfyui", "automatic1111", "leonardo")

SEVERITY: dict[str, int] = {"LIKELY_AUTHENTIC": 0, "POSSIBLY_EDITED": 1, "LIKELY_AI_GENERATED": 2}

VISION_SYSTEM = """You are assisting an insurance claims investigator by looking at one photo from a motor claim.
Look for VISUAL signs that the photo was AI-generated or digitally manipulated, such as:
inconsistent lighting or shadows, impossible damage geometry, warped or unreadable text and number plates,
texture or edge artifacts, cloned or repeated regions.
Rules:
- Your assessment is probabilistic. Never claim certainty. Use hedged wording such as "consistent with".
- Only report indicators you can actually see. Do not invent details.
- Ordinary compression, low resolution, screenshots or poor lighting can look odd without any manipulation.
- The absence of visible signs does not prove the photo is authentic.
Verdicts: LIKELY_AUTHENTIC, POSSIBLY_EDITED, LIKELY_AI_GENERATED.
Return keys: verdict, confidence (0-1), indicators (array of short strings), explanation (1-3 sentences)."""


@dataclass
class ExifAnalysis:
    findings: list[str] = field(default_factory=list)
    editing_tool: bool = False
    ai_tool: bool = False


def analyse_exif(data: bytes, now: datetime | None = None) -> ExifAnalysis:
    """Inspect EXIF tags with Pillow. Returns human-readable findings and tool flags."""
    now = now or datetime.now()
    with Image.open(io.BytesIO(data)) as img:
        exif = img.getexif()

    if not exif:
        return ExifAnalysis(findings=[
            "Weak signal: no EXIF metadata present. Screenshots and messaging apps usually strip it, "
            "so this alone is not evidence of manipulation."
        ])

    result = ExifAnalysis()
    software = str(exif.get(TAG_SOFTWARE, "") or "").strip()
    make = str(exif.get(TAG_MAKE, "") or "").strip()
    model = str(exif.get(TAG_MODEL, "") or "").strip()
    sub_ifd = exif.get_ifd(IFD_EXIF)
    dt_modified = _parse_time(exif.get(TAG_DATETIME))
    dt_original = _parse_time(sub_ifd.get(TAG_DATETIME_ORIGINAL))

    if software:
        lowered = software.lower()
        if any(tool in lowered for tool in AI_TOOLS):
            result.ai_tool = True
            result.findings.append(f"Software tag names an AI image generator: '{software}'")
        elif any(tool in lowered for tool in EDITING_TOOLS):
            result.editing_tool = True
            result.findings.append(f"Software tag names an editing application: '{software}'")
        else:
            result.findings.append(f"Software tag present: '{software}' (not a known editor)")

    if not make and not model:
        result.findings.append(
            "Weak signal: no camera make or model recorded (common for phone exports and edited files)."
        )

    if dt_original is None and dt_modified is None:
        result.findings.append("Weak signal: no capture timestamp recorded.")
    if dt_original and dt_modified and dt_original > dt_modified:
        result.findings.append(
            "Inconsistent timestamps: capture time is later than the last-modified time."
        )
    latest = max((t for t in (dt_original, dt_modified) if t), default=None)
    if latest and latest > now:
        result.findings.append("Inconsistent timestamps: recorded time is in the future.")

    return result


def _parse_time(value: object) -> datetime | None:
    try:
        return datetime.strptime(str(value).strip(), EXIF_TIME_FORMAT)
    except (TypeError, ValueError):
        return None


def _escalate(verdict: AIVerdict, exif: ExifAnalysis) -> tuple[AIVerdict, list[str]]:
    """Deterministic floor from EXIF tool tags: an editor or AI tag means at least POSSIBLY_EDITED."""
    extra: list[str] = []
    if verdict == "LIKELY_AUTHENTIC" and (exif.ai_tool or exif.editing_tool):
        verdict = "POSSIBLY_EDITED"
        extra.append("Verdict raised from LIKELY_AUTHENTIC because of an editing/AI tool tag in EXIF.")
    return verdict, extra


async def _check_one(index: int, data: bytes, narrative: str) -> PerImageAI:
    """Run EXIF plus the vision call for one photo. Never raises for Claude failures."""
    exif = analyse_exif(data)

    if get_settings().mock_mode:
        llm: AIImageLLM | None = mock_ai_image(narrative, exif.findings)
    else:
        context = "EXIF findings for this photo (weak signals unless marked otherwise):\n" + (
            "\n".join(f"- {f}" for f in exif.findings) or "- none"
        )
        try:
            llm = await ask_json(
                AIImageLLM,
                VISION_SYSTEM,
                [image_block(prepare_for_claude(data)), text_block(context)],
            )
        except ClaudeError:
            return PerImageAI(
                image_index=index,
                verdict="UNAVAILABLE",
                confidence=0.0,
                indicators=[],
                exif_findings=exif.findings,
                explanation="The photo check could not be completed (AI service unavailable or invalid reply).",
            )

    verdict, extra = _escalate(llm.verdict, exif)
    return PerImageAI(
        image_index=index,
        verdict=verdict,
        confidence=llm.confidence,
        indicators=llm.indicators + extra,
        exif_findings=exif.findings,
        explanation=llm.explanation,
    )


async def check_ai_generated(images: list[bytes], narrative: str) -> AIGeneratedDetector:
    """Check every photo in parallel and combine into one detector result."""
    per_image = await asyncio.gather(
        *(_check_one(i, data, narrative) for i, data in enumerate(images, start=1))
    )
    return _combine(list(per_image))


def _combine(per_image: list[PerImageAI]) -> AIGeneratedDetector:
    """Overall verdict is the most severe available per-image verdict."""
    available = [p for p in per_image if p.verdict != "UNAVAILABLE"]
    if not available:
        return AIGeneratedDetector(
            verdict="UNAVAILABLE",
            confidence=0.0,
            summary="The AI-generated/edited check could not be completed for any photo.",
            per_image=per_image,
        )
    worst = max(available, key=lambda p: (SEVERITY[p.verdict], p.confidence))
    parts = "; ".join(
        f"photo {p.image_index}: {p.verdict} ({p.confidence:.2f})" if p.verdict != "UNAVAILABLE"
        else f"photo {p.image_index}: unavailable"
        for p in per_image
    )
    return AIGeneratedDetector(
        verdict=worst.verdict,
        confidence=worst.confidence,
        summary=f"Probabilistic screening only, not proof of manipulation. {parts}.",
        per_image=per_image,
    )

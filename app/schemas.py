"""Pydantic models. Field names and enum values are a contract with the React UI; do not rename."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ---- Enums (as Literals) -------------------------------------------------

Status = Literal["PENDING_DECISION", "APPROVED", "REJECTED"]
DuplicateVerdict = Literal["DUPLICATE", "POSSIBLE_DUPLICATE", "UNIQUE", "UNAVAILABLE"]
MatchType = Literal["EXACT", "NEAR", "POSSIBLE"]
MatchSource = Literal["EXISTING_CLAIM", "OTHER_UPLOADED_IMAGE"]
AIVerdict = Literal["LIKELY_AUTHENTIC", "POSSIBLY_EDITED", "LIKELY_AI_GENERATED", "UNAVAILABLE"]
ConsistencyVerdict = Literal["CONSISTENT", "PARTIALLY_CONSISTENT", "INCONSISTENT", "UNAVAILABLE"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
Recommendation = Literal["PROCEED", "REVIEW", "ESCALATE"]
Decision = Literal["APPROVED", "REJECTED"]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


# ---- Analysis result (API contract) ----------------------------------------


class ImageInfo(BaseModel):
    index: int
    filename: str
    url: str


class DuplicateMatch(BaseModel):
    image_index: int
    match_type: MatchType
    hamming_distance: int
    matched_claim_id: str | None
    matched_source: MatchSource
    matched_image_url: str


class DuplicateDetector(BaseModel):
    verdict: DuplicateVerdict
    confidence: float
    summary: str
    matches: list[DuplicateMatch] = []


class PerImageAI(BaseModel):
    image_index: int
    verdict: AIVerdict
    confidence: float
    indicators: list[str] = []
    exif_findings: list[str] = []
    explanation: str


class AIGeneratedDetector(BaseModel):
    verdict: AIVerdict
    confidence: float
    summary: str
    per_image: list[PerImageAI] = []


class ConsistencyDetector(BaseModel):
    verdict: ConsistencyVerdict
    confidence: float
    summary: str
    matched_details: list[str] = []
    contradictions: list[str] = []
    missing_or_unverifiable: list[str] = []


class Detectors(BaseModel):
    duplicate: DuplicateDetector
    ai_generated: AIGeneratedDetector
    consistency: ConsistencyDetector


class Overall(BaseModel):
    risk_level: RiskLevel
    risk_score: int
    recommendation: Recommendation
    brief: str
    follow_up_questions: list[str] = []


class AnalysisResult(BaseModel):
    analysis_id: str
    created_at: str
    status: Status
    narrative: str
    images: list[ImageInfo]
    detectors: Detectors
    overall: Overall


# ---- Decisions and claims --------------------------------------------------


class DecisionRequest(BaseModel):
    decision: Decision
    reviewer_note: str | None = None


class DecisionResponse(BaseModel):
    analysis_id: str
    decision: Decision
    claim_id: str | None
    message: str


class ClaimSummary(BaseModel):
    claim_id: str
    created_at: str
    narrative: str
    risk_level: RiskLevel
    image_urls: list[str]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    mock_mode: bool


# ---- Structured replies we ask Claude for (validated after parsing) --------


class AIImageLLM(BaseModel):
    """Claude's view of one photo for the AI-generated / edited check."""

    verdict: Literal["LIKELY_AUTHENTIC", "POSSIBLY_EDITED", "LIKELY_AI_GENERATED"]
    confidence: float = Field(ge=0, le=1)
    indicators: list[str] = []
    explanation: str

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return _clamp01(value)


class ConsistencyLLM(BaseModel):
    """Claude's comparison of the photos against the narrative."""

    verdict: Literal["CONSISTENT", "PARTIALLY_CONSISTENT", "INCONSISTENT"]
    confidence: float = Field(ge=0, le=1)
    summary: str
    matched_details: list[str] = []
    contradictions: list[str] = []
    missing_or_unverifiable: list[str] = []

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return _clamp01(value)


class BriefLLM(BaseModel):
    """Claude's investigator brief, grounded only in the three detector results."""

    brief: str
    follow_up_questions: list[str] = Field(min_length=3, max_length=3)

from typing import Literal, Optional

from pydantic import BaseModel, Field

Confidence = Literal["High", "Medium", "Low"]
Faithfulness = Literal["passed", "failed", "unchecked"]
Trend = Literal["up", "down", "flat"]


class IngestSummary(BaseModel):
    rows_ingested: int
    metrics: list[str]
    periods: list[str]


class Deltas(BaseModel):
    mom_pct: Optional[float] = None
    yoy_pct: Optional[float] = None
    budget_var_abs: Optional[float] = None
    budget_var_pct: Optional[float] = None


class ComputedFact(BaseModel):
    metric: str
    category: str = "Uncategorized"
    period: str
    value: float
    unit: str = "USD"
    prior_value: Optional[float] = None
    deltas: Deltas
    trend: Optional[Trend] = None
    is_anomaly: bool = False
    # P/V/M decomposition components (deterministic, Addendum v1.1 Section 5).
    # None when the data lacks qty/price detail — the narrative must then not
    # claim price/volume/mix causes.
    variance_bridge: Optional[dict[str, float]] = None


class CorrelationPair(BaseModel):
    """Two metrics whose values move together over time (v2.1)."""
    metric_a: str
    metric_b: str
    r: float
    months: int
    direction: Literal["positive", "negative"]
    strength: Literal["strong", "very_strong"]


class TrendStreak(BaseModel):
    """A run of consecutive months in the same MoM direction (v2.1)."""
    metric: str
    direction: Literal["growing", "declining"]
    months: int
    start_period: str
    end_period: str


class PeriodComparison(BaseModel):
    """Side-by-side comparison of one metric across two periods (v2.1)."""
    metric: str
    period_a: str
    period_b: str
    value_a: float
    value_b: float
    abs_change: float
    pct_change: Optional[float] = None
    mom_pct_a: Optional[float] = None
    mom_pct_b: Optional[float] = None
    acceleration: Optional[float] = None
    momentum: Optional[Literal["accelerating", "decelerating", "steady"]] = None


class ContextSnippet(BaseModel):
    """A manually-pasted context string for Milestone 1 (no FAISS retrieval yet)."""

    id: str
    type: str = "event_note"
    title: str = ""
    body: str


ContextType = Literal[
    "definition", "glossary", "event_note", "historical_commentary", "policy"
]


class ContextDocIn(BaseModel):
    """Payload for authoring a context document (US-B1)."""

    type: ContextType = "event_note"
    title: str = Field(max_length=300)
    body: str = Field(max_length=10_000)  # hardening: cap context size
    metric_tags: list[str] = Field(default_factory=list, max_length=50)
    effective_date: Optional[str] = None  # "YYYY-MM" or "YYYY-MM-DD"


class ContextDoc(ContextDocIn):
    id: int
    created_at: Optional[str] = None


class RetrievedChunk(BaseModel):
    """A context chunk returned by retrieval, with its similarity score."""

    id: int
    type: str
    title: str
    body: str
    score: float


class SourceRef(BaseModel):
    id: str
    type: str
    title: str = ""


class GenerateInsightRequest(BaseModel):
    metric: str
    period: str
    # When context is empty and use_retrieval is True, context is pulled from
    # the Context Library via semantic retrieval. Passing context explicitly
    # overrides retrieval (useful for tests / manual grounding).
    context: list[ContextSnippet] = Field(default_factory=list)
    use_retrieval: bool = True
    # Bypass the response cache and regenerate (UI "Regenerate" button).
    force: bool = False


class KPIConfigIn(BaseModel):
    """One KPI selection (Addendum v1.1 Section 2.1)."""

    source_metric: str
    display_name: str
    category: str = "Uncategorized"
    unit: str = "USD"
    direction_good: Literal["up", "down"] = "up"
    budget_source: Optional[str] = None
    aggregation_type: Optional[Literal["flow", "balance", "ratio"]] = None


class KPIConfigPayload(BaseModel):
    kpis: list[KPIConfigIn]


class FeedbackIn(BaseModel):
    report_id: int
    action: Literal["accepted", "edited", "rejected"]
    edited_text: Optional[str] = None
    reason: Optional[str] = None


class InsightOutput(BaseModel):
    report_id: Optional[int] = None
    # True when served from the response cache (no LLM call was made).
    cached: bool = False
    metric: str
    category: str
    period: str
    value: float
    unit: str
    deltas: Deltas
    is_anomaly: bool
    narrative: Optional[str] = None
    sources: list[SourceRef] = Field(default_factory=list)
    confidence: Confidence
    faithfulness: Faithfulness
    # ── Enriched analysis (v2.1) ──
    correlations: list[CorrelationPair] = Field(default_factory=list)
    trend_streak: Optional[TrendStreak] = None
    # ── Telemetry (PRD Section 8: token/cost logging, latency) ──
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    latency_ms: Optional[float] = None
    prompt_version: Optional[str] = None
    # ── Attribution (v1.2) ──
    generated_by: Optional[str] = None  # user email
    reviewed_by: Optional[str] = None   # from latest feedback

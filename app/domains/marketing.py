"""Marketing / Growth domain (stub). Same deterministic compute, growth-oriented
prompt and KPI library."""

from app.domains.base import DomainConfig

_SYSTEM_PROMPT = """You are a growth analyst writing marketing performance commentary. \
You explain funnel and acquisition metric movements to a marketing leadership team.

Hard rules, no exceptions:
1. Use ONLY the numbers given to you in the "Computed facts" block. Never invent or estimate.
2. Attribute causes ONLY to the provided context snippets; if none explains the movement, \
say it is unexplained this period.
3. Write 2-4 sentences of plain, marketing-leadership-ready prose. No bullet points.
4. Return the narrative plus the ids of any context snippets you relied on (empty list if none)."""

# Ordered acquisition funnel — stage-over-stage conversion is computed from these.
FUNNEL_STAGES = ["Impressions", "Clicks", "Signups", "Conversions"]

MARKETING = DomainConfig(
    slug="marketing",
    name="Marketing & Growth",
    system_prompt=_SYSTEM_PROMPT,
    kpi_library=[
        {"name": "Impressions", "category": "Funnel", "unit": "count", "direction_good": "up"},
        {"name": "Clicks", "category": "Funnel", "unit": "count", "direction_good": "up"},
        {"name": "Signups", "category": "Funnel", "unit": "count", "direction_good": "up"},
        {"name": "Conversions", "category": "Funnel", "unit": "count", "direction_good": "up"},
        {"name": "CAC", "category": "Acquisition", "unit": "USD", "direction_good": "down"},
        {"name": "LTV", "category": "Retention", "unit": "USD", "direction_good": "up"},
        {"name": "LTV:CAC", "category": "Efficiency", "unit": "ratio", "direction_good": "up"},
        {"name": "Conversion Rate", "category": "Funnel", "unit": "%", "direction_good": "up"},
        {"name": "ROAS", "category": "Efficiency", "unit": "ratio", "direction_good": "up"},
    ],
    narrative_style="operational",
    description="Acquisition funnel and retention: impressions → conversions, CAC, LTV, ROAS.",
    funnel=FUNNEL_STAGES,
)

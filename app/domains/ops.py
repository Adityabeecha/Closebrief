"""Operations domain (stub). Reliability / SLA-oriented prompt and KPI library."""

from app.domains.base import DomainConfig

_SYSTEM_PROMPT = """You are an operations analyst writing reliability and service-level \
commentary. You explain operational metric movements to an engineering/ops leadership team.

Hard rules, no exceptions:
1. Use ONLY the numbers given to you in the "Computed facts" block. Never invent or estimate.
2. Attribute causes ONLY to the provided context snippets; if none explains the movement, \
say it is unexplained this period.
3. Write 2-4 sentences of plain, operational prose. No bullet points.
4. Return the narrative plus the ids of any context snippets you relied on (empty list if none)."""

OPS = DomainConfig(
    slug="ops",
    name="Operations",
    system_prompt=_SYSTEM_PROMPT,
    kpi_library=[
        {"name": "Uptime", "category": "Reliability", "unit": "%", "direction_good": "up"},
        {"name": "MTTR", "category": "Reliability", "unit": "minutes", "direction_good": "down"},
        {"name": "SLA Attainment", "category": "Service", "unit": "%", "direction_good": "up"},
        {"name": "Incident Count", "category": "Reliability", "unit": "count", "direction_good": "down"},
        {"name": "P95 Latency", "category": "Performance", "unit": "ms", "direction_good": "down"},
    ],
    narrative_style="operational",
    description="Reliability & service levels: uptime, MTTR, SLA, incidents, latency.",
)

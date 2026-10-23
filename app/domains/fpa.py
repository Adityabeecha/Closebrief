"""FP&A domain — the default. Reuses the canonical system prompt and the
built-in KPI library so existing behavior is unchanged."""

from app.domains.base import DomainConfig
from app.generation.prompts import SYSTEM_PROMPT
from app.kpis.library import library_listing

FPA = DomainConfig(
    slug="fpa",
    name="FP&A",
    system_prompt=SYSTEM_PROMPT,
    kpi_library=library_listing(),
    narrative_style="executive",
    description="Financial planning & analysis: revenue, margin, spend, budget variance.",
)

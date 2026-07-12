"""Domain configuration contract. A DomainConfig bundles the domain-specific
prompt and KPI library; compute (`app/compute/*`) stays domain-agnostic."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DomainConfig:
    slug: str                       # stable id, e.g. "fpa"
    name: str                       # display name, e.g. "FP&A"
    system_prompt: str              # domain-specific LLM system prompt
    kpi_library: list[dict] = field(default_factory=list)
    narrative_style: str = "executive"   # "executive" | "operational"
    description: str = ""
    # Ordered funnel stages (metric names) for stage-over-stage analysis, e.g.
    # ["Impressions", "Clicks", "Signups", "Conversions"]. Empty = no funnel.
    funnel: list[str] = field(default_factory=list)

    def public(self) -> dict:
        """Serializable summary for the /domains endpoint (no prompt text)."""
        return {
            "slug": self.slug,
            "name": self.name,
            "narrative_style": self.narrative_style,
            "description": self.description,
            "kpi_count": len(self.kpi_library),
            "funnel": self.funnel,
        }

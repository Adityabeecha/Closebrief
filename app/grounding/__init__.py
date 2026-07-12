"""Grounding: the single source of truth for how a narrative traces back to
computed facts and context documents. The faithfulness guard and the UI
drill-down both build on the same primitives (app.generation.guard)."""

from app.grounding.attribution import attribute

__all__ = ["attribute"]

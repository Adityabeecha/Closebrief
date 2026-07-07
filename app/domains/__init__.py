"""Pluggable analytics domains (v2.1 foundation). A domain supplies a
domain-specific system prompt and KPI library; the deterministic compute layer
is shared. FP&A is the default; Marketing and Operations are stubs that prove
the extension point."""

from app.domains.registry import DEFAULT_DOMAIN, get_domain, list_domains, registry

__all__ = ["registry", "get_domain", "list_domains", "DEFAULT_DOMAIN"]

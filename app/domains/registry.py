"""Domain registry: slug -> DomainConfig. Import a domain module to register it."""

from __future__ import annotations

from app.domains.base import DomainConfig
from app.domains.fpa import FPA
from app.domains.marketing import MARKETING
from app.domains.ops import OPS

DEFAULT_DOMAIN = "fpa"


class DomainRegistry:
    def __init__(self, domains: list[DomainConfig]):
        self._by_slug = {d.slug: d for d in domains}

    def get(self, slug: str | None) -> DomainConfig:
        """Return the domain for `slug`, falling back to the default."""
        return self._by_slug.get(slug or DEFAULT_DOMAIN, self._by_slug[DEFAULT_DOMAIN])

    def has(self, slug: str) -> bool:
        return slug in self._by_slug

    def list(self) -> list[DomainConfig]:
        return list(self._by_slug.values())


registry = DomainRegistry([FPA, MARKETING, OPS])


def get_domain(slug: str | None) -> DomainConfig:
    return registry.get(slug)


def list_domains() -> list[DomainConfig]:
    return registry.list()

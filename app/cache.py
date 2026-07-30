"""Response cache for LLM generations (v1.1 Phase 3).

Identical /generate-insight requests are served from cache instead of
re-paying LLM cost. The key hashes everything that could change the answer:
metric, period, the computed facts themselves, the retrieved context (ids +
content), prompt version, provider, and model.

Invalidation is namespace-based: every data-changing operation (ingest,
mapping, compute, KPI config, context CRUD) bumps a namespace counter, which
orphans all previous keys; orphans expire via TTL. This avoids SCAN/DEL
sweeps, which Upstash bills per command.

Backends: RedisCache (REDIS_URL set — Upstash) or InMemoryCache (local dev /
tests). Both behind the Cache protocol.
"""

import hashlib
import json
import logging
import time
from typing import Any, Optional, Protocol

from app.config import settings


class Cache(Protocol):
    backend: str

    def get(self, key: str) -> Optional[dict]: ...
    def set(self, key: str, value: dict, ttl_seconds: int) -> None: ...
    def bump_namespace(self) -> None: ...
    def ping(self) -> bool: ...
    def record(self, hit: bool) -> None: ...
    def stats(self) -> dict: ...


def make_insight_key(
    metric: str,
    period: str,
    fact_payload: Any,
    context_ids_and_bodies: Any,
    prompt_version: str,
    provider: str,
    model: str,
) -> str:
    raw = json.dumps(
        {
            "metric": metric,
            "period": period,
            "facts": fact_payload,
            "context": context_ids_and_bodies,
            "prompt_version": prompt_version,
            "provider": provider,
            "model": model,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class RedisCache:
    backend = "redis"
    _NS_KEY = "cb:cache:ns"

    # The namespace counter is memoized for a few seconds so a cache hit costs
    # one Upstash round trip, not two. Worst case: a data change made in the
    # last 5s serves one stale (still well-formed) cached narrative.
    _NS_MEMO_SECONDS = 5.0

    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.from_url(url, socket_timeout=10, decode_responses=True)
        self._ns_value: Optional[str] = None
        self._ns_fetched_at = 0.0

    def _ns(self) -> str:
        now = time.time()
        if self._ns_value is None or now - self._ns_fetched_at > self._NS_MEMO_SECONDS:
            self._ns_value = self._r.get(self._NS_KEY) or "0"
            self._ns_fetched_at = now
        return self._ns_value

    def _full(self, key: str) -> str:
        return f"cb:{self._ns()}:{key}"

    def get(self, key: str) -> Optional[dict]:
        try:
            raw = self._r.get(self._full(key))
            return json.loads(raw) if raw else None
        except Exception:
            return None  # cache is an optimization — never fail the request

    def set(self, key: str, value: dict, ttl_seconds: int) -> None:
        try:
            self._r.set(self._full(key), json.dumps(value, default=str), ex=ttl_seconds)
        except Exception:
            pass

    def bump_namespace(self) -> None:
        try:
            self._ns_value = str(self._r.incr(self._NS_KEY))
            self._ns_fetched_at = time.time()
        except Exception:
            pass

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:
            return False

    def record(self, hit: bool) -> None:
        try:
            self._r.incr("cb:stats:hit" if hit else "cb:stats:miss")
        except Exception:
            pass

    def stats(self) -> dict:
        try:
            hits = int(self._r.get("cb:stats:hit") or 0)
            misses = int(self._r.get("cb:stats:miss") or 0)
        except Exception:
            hits = misses = 0
        total = hits + misses
        return {
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total, 3) if total else None,
        }


class InMemoryCache:
    backend = "memory"

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, dict]] = {}
        self._ns = 0
        self._hits = 0
        self._misses = 0

    def _full(self, key: str) -> str:
        return f"{self._ns}:{key}"

    def get(self, key: str) -> Optional[dict]:
        item = self._data.get(self._full(key))
        if item is None:
            return None
        expires, value = item
        if time.time() > expires:
            self._data.pop(self._full(key), None)
            return None
        return value

    def set(self, key: str, value: dict, ttl_seconds: int) -> None:
        self._data[self._full(key)] = (time.time() + ttl_seconds, value)

    def bump_namespace(self) -> None:
        self._ns += 1

    def ping(self) -> bool:
        return True

    def record(self, hit: bool) -> None:
        if hit:
            self._hits += 1
        else:
            self._misses += 1

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else None,
        }


class DisabledCache:
    backend = "disabled"

    def get(self, key: str) -> Optional[dict]:
        return None

    def set(self, key: str, value: dict, ttl_seconds: int) -> None:
        pass

    def bump_namespace(self) -> None:
        pass

    def ping(self) -> bool:
        return True

    def record(self, hit: bool) -> None:
        pass

    def stats(self) -> dict:
        return {"hits": 0, "misses": 0, "hit_rate": None}


def get_cache() -> Cache:
    if not settings.cache_enabled:
        return DisabledCache()
    if settings.redis_url:
        try:
            return RedisCache(settings.redis_url)
        except Exception:  # noqa: BLE001
            logging.getLogger("closebrief").warning(
                "REDIS_URL is unusable — falling back to the in-process cache", exc_info=True)
    return InMemoryCache()

#!/usr/bin/env python3
"""
WaterfallHunter - Provider Registry & Failover Management System v4.2 (Debugged)
Strictly enforces Provider Failover Policy.
"""

from __future__ import annotations
import asyncio
import logging
import random
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Coroutine, Dict, Optional, Set, Any

from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.schema_contract import require_managed_schema

logger = logging.getLogger("WaterfallHunter.ProviderRegistry")

class ProviderRole(str, Enum):
    CATALOGUE_DISCOVERY = "discovery_catalogue"
    MARKET_DATA = "market_data"
    PRIMARY_ANALYSIS = "primary_analysis"
    CONFIRMATION = "confirmation"
    INTELLIGENCE = "intelligence"

class FailureClass(str, Enum):
    NONE = "none"
    TRANSIENT = "transient"
    SEMANTIC = "semantic"
    UNSUPPORTED = "unsupported"
    AUTH_REQUIRED = "auth_required"

class ProviderStatus(str, Enum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    DISABLED_PENDING_REVIEW = "disabled_pending_review"

@dataclass
class ProviderMetadata:
    provider_id: str
    upstream_identity: str
    roles: Set[ProviderRole]
    capabilities: Set[str]
    priority: int = 100
    status: ProviderStatus = ProviderStatus.ACTIVE
    failure_class: FailureClass = FailureClass.NONE
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0
    replacement_generation: int = 0
    last_attempt_at: float = 0.0
    last_success_at: float = 0.0

    def is_available(self, role: ProviderRole) -> bool:
        if role not in self.roles:
            return False
        if self.status != ProviderStatus.ACTIVE:
            return False
        if time.time() < self.circuit_open_until:
            return False
        return True

class StorageAdapter:
    def __init__(
        self,
        db_path: str = "/app/data/waterfall_registry.db",
        *,
        verify_schema: bool = True,
    ):
        self.db_path = db_path
        if verify_schema:
            require_managed_schema(
                self.db_path,
                required_tables=frozenset({"provider_states"}),
            )

    def persist_provider_state(self, p: ProviderMetadata):
        with connect_managed_sqlite(self.db_path) as conn:
            conn.execute("""
                INSERT INTO provider_states (
                    provider_id, upstream_identity, status, failure_class,
                    consecutive_failures, circuit_open_until, replacement_generation,
                    last_success_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    status=excluded.status,
                    failure_class=excluded.failure_class,
                    consecutive_failures=excluded.consecutive_failures,
                    circuit_open_until=excluded.circuit_open_until,
                    replacement_generation=excluded.replacement_generation,
                    last_success_at=excluded.last_success_at,
                    updated_at=excluded.updated_at
            """, (
                p.provider_id, p.upstream_identity, p.status.value, p.failure_class.value,
                p.consecutive_failures, p.circuit_open_until, p.replacement_generation,
                p.last_success_at, time.time()
            ))
            conn.commit()

    def load_provider_states(self) -> Dict[str, dict]:
        with connect_managed_sqlite(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM provider_states")
            return {row["provider_id"]: dict(row) for row in cursor.fetchall()}

class ProviderRegistry:
    def __init__(self, storage: Optional[StorageAdapter] = None):
        self.providers: Dict[str, ProviderMetadata] = {}
        self.storage = storage or StorageAdapter()
        self._cached_states = self.storage.load_provider_states()

    def register_provider(self, p: ProviderMetadata):
        # Violation LBANK-001 Check
        if "lbank" in p.provider_id.lower() or "lbank" in p.upstream_identity.lower():
            if p.roles != {ProviderRole.CATALOGUE_DISCOVERY}:
                raise ValueError(
                    f"Violation LBANK-001: LBank provider '{p.provider_id}' "
                    f"can only be registered for CATALOGUE_DISCOVERY. Got: {p.roles}"
                )

        persisted = self._cached_states.get(p.provider_id)
        if persisted:
            p.status = ProviderStatus(persisted["status"])
            p.failure_class = FailureClass(persisted["failure_class"])
            p.consecutive_failures = persisted["consecutive_failures"]
            p.circuit_open_until = persisted["circuit_open_until"]
            p.replacement_generation = persisted["replacement_generation"]
            p.last_success_at = persisted["last_success_at"]
        self.providers[p.provider_id] = p

    def get_active_provider(
        self,
        role: ProviderRole,
        exclude_upstream: Optional[str] = None,
        exclude_provider_ids: Optional[Set[str]] = None
    ) -> Optional[ProviderMetadata]:
        # BUG-01 Fix: اضافه شدن فیلتر exclude_provider_ids برای جلوگیری از حلقه تکرار
        exclude_ids = exclude_provider_ids or set()
        candidates = [
            p for p in self.providers.values()
            if p.is_available(role)
            and p.provider_id not in exclude_ids
            and (not exclude_upstream or p.upstream_identity != exclude_upstream)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x.priority)
        return candidates[0]

    async def execute_with_failover(
        self,
        role: ProviderRole,
        capability: str,
        fetch_factory: Callable[[ProviderMetadata], Coroutine[Any, Any, Any]],
        exclude_upstream: Optional[str] = None
    ) -> Any:
        max_candidates = 3
        evaluated_candidates = 0
        tried_provider_ids: Set[str] = set()

        while evaluated_candidates < max_candidates:
            provider = self.get_active_provider(
                role,
                exclude_upstream=exclude_upstream,
                exclude_provider_ids=tried_provider_ids
            )
            if not provider:
                break

            evaluated_candidates += 1
            tried_provider_ids.add(provider.provider_id)
            provider.last_attempt_at = time.time()

            # BUG-02 Fix: استفاده از fetch_factory به جای پاس دادن مستقیم coroutine
            success, result, failure_type = await self._attempt_provider_call(provider, fetch_factory)

            if success:
                provider.consecutive_failures = 0
                provider.failure_class = FailureClass.NONE
                provider.last_success_at = time.time()
                self.storage.persist_provider_state(provider)
                return result

            self._handle_provider_failure(provider, failure_type)
            self.storage.persist_provider_state(provider)

        raise RuntimeError("BLOCKED_PROVIDER_POOL_EXHAUSTED")

    async def _attempt_provider_call(
        self,
        provider: ProviderMetadata,
        fetch_factory: Callable[[ProviderMetadata], Coroutine[Any, Any, Any]]
    ):
        max_transient_attempts = 3
        for attempt in range(1, max_transient_attempts + 1):
            try:
                coro = fetch_factory(provider)
                result = await asyncio.wait_for(coro, timeout=8.0)
                return True, result, FailureClass.NONE
            except asyncio.TimeoutError:
                if attempt == max_transient_attempts:
                    return False, None, FailureClass.TRANSIENT
                await asyncio.sleep(0.05 * attempt + random.uniform(0, 0.02))
            except ValueError:
                return False, None, FailureClass.SEMANTIC
            except PermissionError:
                return False, None, FailureClass.AUTH_REQUIRED
            except Exception as e:
                logger.error(f"Provider {provider.provider_id} encountered exception: {e}")
                if attempt == max_transient_attempts:
                    return False, None, FailureClass.TRANSIENT
                await asyncio.sleep(0.05 * attempt)
        return False, None, FailureClass.TRANSIENT

    def _handle_provider_failure(self, provider: ProviderMetadata, failure_type: FailureClass):
        provider.consecutive_failures += 1
        provider.failure_class = failure_type

        if failure_type == FailureClass.TRANSIENT:
            if provider.consecutive_failures >= 3:
                provider.status = ProviderStatus.QUARANTINED
                provider.circuit_open_until = time.time() + 900.0
                provider.replacement_generation += 1
                logger.warning(f"Provider {provider.provider_id} quarantined.")
        elif failure_type in (FailureClass.SEMANTIC, FailureClass.UNSUPPORTED, FailureClass.AUTH_REQUIRED):
            provider.status = ProviderStatus.DISABLED_PENDING_REVIEW
            provider.replacement_generation += 1
            logger.error(f"Provider {provider.provider_id} disabled due to {failure_type.value}.")

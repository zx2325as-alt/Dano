"""Short-lived opaque option-reference storage.

Only a random bearer reference reaches the browser. Target-system values remain in Dano's
store, keyed by a SHA-256 digest of the bearer token. PostgreSQL is the production store;
an explicit memory store exists for isolated tests, never as a silent production fallback.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Protocol

_TOKEN_PREFIX = "oref1_"
_DEFAULT_TTL_SECONDS = 600
_MAX_TTL_SECONDS = 3600


class OptionReferenceError(ValueError):
    code = "invalid_option_reference"


class OptionReferenceUnavailable(OptionReferenceError):
    code = "option_reference_unavailable"


class OptionReferenceExpired(OptionReferenceError):
    code = "option_reference_expired"


class OptionReferenceScopeMismatch(OptionReferenceError):
    code = "option_reference_scope_mismatch"


@dataclass(frozen=True)
class OptionReferenceRecord:
    tenant: str
    skill_id: str
    field: str
    source_fingerprint: str
    value: Any
    label: str
    context_hash: str
    expires_at: float


class OptionReferenceStore(Protocol):
    async def issue(self, record: OptionReferenceRecord) -> str: ...
    async def redeem(self, token: str) -> OptionReferenceRecord: ...


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def reference_ttl_seconds() -> int:
    raw = os.getenv("DANO_OPTION_REFERENCE_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_TTL_SECONDS
    return max(30, min(value, _MAX_TTL_SECONDS))


def new_reference_token() -> str:
    return _TOKEN_PREFIX + secrets.token_urlsafe(32)


def looks_like_reference(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_TOKEN_PREFIX) and 20 <= len(value) <= 256


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


class MemoryOptionReferenceStore:
    """Deterministic injectable store for tests and single-process local development."""

    def __init__(self, *, clock=time.time) -> None:
        self._clock = clock
        self._records: dict[str, OptionReferenceRecord] = {}

    async def issue(self, record: OptionReferenceRecord) -> str:
        token = new_reference_token()
        self._records[token_hash(token)] = copy.deepcopy(record)
        return token

    async def redeem(self, token: str) -> OptionReferenceRecord:
        if not looks_like_reference(token):
            raise OptionReferenceError("候选引用格式无效")
        record = self._records.get(token_hash(token))
        if record is None:
            raise OptionReferenceError("候选引用不存在或已失效")
        if record.expires_at <= self._clock():
            self._records.pop(token_hash(token), None)
            raise OptionReferenceExpired("候选引用已过期，请重新查询候选项")
        return copy.deepcopy(record)


class PgOptionReferenceStore:
    async def issue(self, record: OptionReferenceRecord) -> str:
        try:
            from dano.infra.db import get_pool

            pool = get_pool()
        except Exception as exc:  # noqa: BLE001
            raise OptionReferenceUnavailable("候选引用存储不可用，已阻止返回原始系统 ID") from exc
        token = new_reference_token()
        digest = token_hash(token)
        try:
            await pool.execute(
                "INSERT INTO option_references "
                "(ref_hash, tenant, skill_id, field_name, source_fingerprint, value_json, label, context_hash, expires_at) "
                "VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,to_timestamp($9))",
                digest,
                record.tenant,
                record.skill_id,
                record.field,
                record.source_fingerprint,
                json.dumps(record.value, ensure_ascii=False, separators=(",", ":"), default=str),
                record.label,
                record.context_hash,
                record.expires_at,
            )
        except Exception as exc:  # noqa: BLE001
            raise OptionReferenceUnavailable("候选引用写入失败，已阻止返回原始系统 ID") from exc
        return token

    async def redeem(self, token: str) -> OptionReferenceRecord:
        if not looks_like_reference(token):
            raise OptionReferenceError("候选引用格式无效")
        try:
            from dano.infra.db import get_pool

            pool = get_pool()
        except Exception as exc:  # noqa: BLE001
            raise OptionReferenceUnavailable("候选引用存储不可用") from exc
        try:
            row = await pool.fetchrow(
                "SELECT tenant, skill_id, field_name, source_fingerprint, value_json, label, context_hash, "
                "extract(epoch from expires_at) AS expires_at "
                "FROM option_references WHERE ref_hash=$1",
                token_hash(token),
            )
        except Exception as exc:  # noqa: BLE001
            raise OptionReferenceUnavailable("候选引用读取失败") from exc
        if row is None:
            raise OptionReferenceError("候选引用不存在或已失效")
        expires_at = float(row["expires_at"])
        if expires_at <= time.time():
            try:
                await pool.execute("DELETE FROM option_references WHERE ref_hash=$1", token_hash(token))
            except Exception:  # noqa: BLE001
                pass
            raise OptionReferenceExpired("候选引用已过期，请重新查询候选项")
        return OptionReferenceRecord(
            tenant=str(row["tenant"]),
            skill_id=str(row["skill_id"]),
            field=str(row["field_name"]),
            source_fingerprint=str(row["source_fingerprint"]),
            value=_decode_json(row["value_json"]),
            label=str(row["label"] or ""),
            context_hash=str(row["context_hash"] or ""),
            expires_at=expires_at,
        )


_store: OptionReferenceStore = PgOptionReferenceStore()


def set_option_reference_store(store: OptionReferenceStore) -> None:
    global _store
    _store = store


def get_option_reference_store() -> OptionReferenceStore:
    return _store

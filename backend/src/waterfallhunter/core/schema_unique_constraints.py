from __future__ import annotations

import sqlite3
from dataclasses import dataclass


CANONICAL_UNIQUE_KEYS: dict[str, frozenset[tuple[str, ...]]] = {
    "lbank_signal_outcomes": frozenset({("signal_id",)}),
    "production_evidence_snapshots": frozenset({("bucket_started_at", "symbol")}),
    "production_feature_replay_results_v2": frozenset(
        {("snapshot_id", "replay_version")}
    ),
    "lbank_execution_decision_log": frozenset(
        {("bucket_started_at", "source", "symbol")}
    ),
    "operational_historical_outcome_datasets": frozenset({("report_sha256",)}),
    "operational_historical_signal_outcomes": frozenset({("event_key",)}),
    "signal_decisions": frozenset({("decision_id",)}),
    "domain_outbox_events": frozenset(
        {
            ("event_key",),
            ("aggregate_type", "aggregate_id", "aggregate_version", "event_sequence"),
        }
    ),
    "lifecycle_v2_shadow_events": frozenset({("transition_hash",)}),
    "decision_outcome_capture": frozenset({("decision_event_id",)}),
    "decision_outcome_resolution": frozenset({("decision_event_id",)}),
}


@dataclass(frozen=True, slots=True)
class UniqueConstraintIssue:
    table: str
    expected: tuple[tuple[str, ...], ...]
    actual: tuple[tuple[str, ...], ...]
    detail: str


@dataclass(frozen=True, slots=True)
class UniqueConstraintVerificationResult:
    valid: bool
    issues: tuple[UniqueConstraintIssue, ...]


@dataclass(frozen=True, slots=True)
class _SqlToken:
    kind: str
    value: str


def _sql_tokens(value: str | None) -> tuple[_SqlToken, ...]:
    """Tokenize SQLite DDL while hiding comments and string-literal contents."""
    if not isinstance(value, str):
        return ()

    tokens: list[_SqlToken] = []
    index = 0
    while index < len(value):
        character = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""

        if character.isspace():
            index += 1
            continue
        if character == "-" and following == "-":
            index += 2
            while index < len(value) and value[index] not in "\r\n":
                index += 1
            continue
        if character == "/" and following == "*":
            comment_end = value.find("*/", index + 2)
            if comment_end < 0:
                return ()
            index = comment_end + 2
            continue
        if character == "'":
            index += 1
            while index < len(value):
                if value[index] == "'":
                    if index + 1 < len(value) and value[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    tokens.append(_SqlToken("literal", ""))
                    break
                index += 1
            else:
                return ()
            continue
        if character in {'"', "`", "["}:
            closing = "]" if character == "[" else character
            decoded: list[str] = []
            index += 1
            while index < len(value):
                if value[index] == closing:
                    if index + 1 < len(value) and value[index + 1] == closing:
                        decoded.append(closing)
                        index += 2
                        continue
                    index += 1
                    tokens.append(_SqlToken("identifier", "".join(decoded)))
                    break
                decoded.append(value[index])
                index += 1
            else:
                return ()
            continue
        if character.isalnum() or character in {"_", "$"}:
            end = index + 1
            while end < len(value) and (
                value[end].isalnum() or value[end] in {"_", "$"}
            ):
                end += 1
            tokens.append(_SqlToken("word", value[index:end].casefold()))
            index = end
            continue

        tokens.append(_SqlToken("symbol", character))
        index += 1

    return tuple(tokens)


def _is_keyword(token: _SqlToken, keyword: str) -> bool:
    return token.kind == "word" and token.value == keyword


def _table_entries(sql: str | None) -> tuple[tuple[_SqlToken, ...], ...]:
    tokens = _sql_tokens(sql)
    opening = next(
        (
            index
            for index, token in enumerate(tokens)
            if token.kind == "symbol" and token.value == "("
        ),
        None,
    )
    if opening is None:
        return ()

    entries: list[tuple[_SqlToken, ...]] = []
    entry_start = opening + 1
    depth = 0
    for index in range(entry_start, len(tokens)):
        token = tokens[index]
        if token.kind != "symbol":
            continue
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            if depth == 0:
                if index > entry_start:
                    entries.append(tokens[entry_start:index])
                return tuple(entries)
            depth -= 1
        elif token.value == "," and depth == 0:
            if index > entry_start:
                entries.append(tokens[entry_start:index])
            entry_start = index + 1
    return ()


def _conflict_action(tokens: tuple[_SqlToken, ...], unique_at: int) -> str:
    depth = 0
    for index in range(unique_at + 1, len(tokens) - 2):
        token = tokens[index]
        if token.kind == "symbol":
            if token.value == "(":
                depth += 1
            elif token.value == ")":
                depth = max(depth - 1, 0)
            continue
        if (
            depth == 0
            and _is_keyword(token, "on")
            and _is_keyword(tokens[index + 1], "conflict")
            and tokens[index + 2].kind == "word"
        ):
            return tokens[index + 2].value
    return "abort"


def _table_unique_columns(
    tokens: tuple[_SqlToken, ...],
    unique_at: int,
) -> tuple[str, ...]:
    opening = next(
        (
            index
            for index in range(unique_at + 1, len(tokens))
            if tokens[index].kind == "symbol" and tokens[index].value == "("
        ),
        None,
    )
    if opening is None:
        return ()

    columns: list[str] = []
    part: list[_SqlToken] = []
    depth = 0
    for token in tokens[opening + 1 :]:
        if token.kind == "symbol" and token.value == "(":
            depth += 1
            part.append(token)
            continue
        if token.kind == "symbol" and token.value == ")":
            if depth == 0:
                candidates = [
                    item.value
                    for item in part
                    if item.kind in {"word", "identifier"}
                ]
                if candidates:
                    columns.append(candidates[0])
                return tuple(columns)
            depth -= 1
            part.append(token)
            continue
        if token.kind == "symbol" and token.value == "," and depth == 0:
            candidates = [
                item.value
                for item in part
                if item.kind in {"word", "identifier"}
            ]
            if not candidates:
                return ()
            columns.append(candidates[0])
            part.clear()
            continue
        part.append(token)
    return ()


def _declared_unique_constraints(
    conn: sqlite3.Connection,
    table: str,
) -> tuple[tuple[tuple[str, ...], str], ...]:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row is None:
        return ()

    constraints: list[tuple[tuple[str, ...], str]] = []
    for entry in _table_entries(row[0]):
        cursor = 0
        if entry and _is_keyword(entry[0], "constraint"):
            cursor = 2
        if cursor < len(entry) and _is_keyword(entry[cursor], "unique"):
            columns = _table_unique_columns(entry, cursor)
            if columns:
                constraints.append((columns, _conflict_action(entry, cursor)))
            continue
        if not entry or entry[0].kind not in {"word", "identifier"}:
            continue

        depth = 0
        for index in range(1, len(entry)):
            token = entry[index]
            if token.kind == "symbol":
                if token.value == "(":
                    depth += 1
                elif token.value == ")":
                    depth = max(depth - 1, 0)
                continue
            if depth == 0 and _is_keyword(token, "unique"):
                constraints.append(
                    ((entry[0].value,), _conflict_action(entry, index))
                )
                break
    return tuple(sorted(constraints))


def _actual_unique_keys(
    conn: sqlite3.Connection,
    table: str,
) -> frozenset[tuple[str, ...]]:
    """Return non-primary-key UNIQUE keys using SQLite structural metadata."""
    keys: set[tuple[str, ...]] = set()
    rows = conn.execute(f'PRAGMA index_list("{table}")').fetchall()
    for row in rows:
        if len(row) < 3 or int(row[2]) != 1:
            continue
        origin = str(row[3]).casefold() if len(row) >= 4 else ""
        if origin == "pk":
            continue
        index_name = str(row[1])
        columns = tuple(
            str(index_row[2])
            for index_row in conn.execute(
                f'PRAGMA index_info("{index_name}")'
            ).fetchall()
            if index_row[2] is not None
        )
        if columns:
            keys.add(columns)
    return frozenset(keys)


def verify_unique_constraints_connection(
    conn: sqlite3.Connection,
    *,
    tables: frozenset[str] | None = None,
) -> UniqueConstraintVerificationResult:
    """Verify the exact canonical UNIQUE-key set without mutating SQLite."""
    selected = frozenset(CANONICAL_UNIQUE_KEYS) if tables is None else frozenset(tables)
    existing = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    issues: list[UniqueConstraintIssue] = []
    for table in sorted(selected):
        if table not in existing:
            # Missing-table handling belongs to the primary schema verifier.
            continue
        expected = CANONICAL_UNIQUE_KEYS.get(table, frozenset())
        actual = _actual_unique_keys(conn, table)
        expected_actions = tuple(sorted((key, "abort") for key in expected))
        actual_actions = _declared_unique_constraints(conn, table)
        if actual != expected or actual_actions != expected_actions:
            issues.append(
                UniqueConstraintIssue(
                    table=table,
                    expected=tuple(sorted(expected)),
                    actual=tuple(sorted(actual)),
                    detail=(
                        "expected UNIQUE constraints with ABORT conflict action "
                        f"{expected_actions!r}; found {actual_actions!r}"
                    ),
                )
            )
    return UniqueConstraintVerificationResult(
        valid=not issues,
        issues=tuple(issues),
    )

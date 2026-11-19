"""Packaged read-only warehouse source SQL referenced by the metric registry."""

from importlib.resources import files
import copy
import hashlib
import re

from .errors import reject

RESOURCE = "sql/warehouse_queries.sql"
MAX_RESOURCE_BYTES = 131_072
MAX_QUERY_BYTES = 32_768
_QUERY_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_METRIC_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def _read_resource(raw_text: str | None) -> str:
    try:
        text = (
            files("growthos").joinpath(RESOURCE).read_text(encoding="utf-8")
            if raw_text is None else raw_text
        )
    except (OSError, UnicodeError):
        reject("SCHEMA", "Packaged warehouse query source is unreadable.")
    if (
        not isinstance(text, str) or not text
        or len(text.encode("utf-8")) > MAX_RESOURCE_BYTES
        or any(not c.isprintable() and c not in "\n\r\t" for c in text)
    ):
        reject("SCHEMA", "Packaged warehouse query source exceeds its reviewed bounds.")
    return text


def _finish_query(queries: dict, name: str | None, metrics: list[str] | None, body: list[str]) -> None:
    if name is None:
        return
    if name in queries:
        reject("SCHEMA", "Duplicate warehouse query identifier.")
    if not _QUERY_ID.fullmatch(name):
        reject("SCHEMA", "Invalid warehouse query identifier.")
    if not metrics:
        reject("SCHEMA", "Warehouse query must declare at least one metric.")
    if any(not _METRIC_ID.fullmatch(metric) for metric in metrics):
        reject("SCHEMA", "Invalid metric identifier in warehouse query header.")
    if len(set(metrics)) != len(metrics):
        reject("SCHEMA", "Duplicate metric identifier in warehouse query header.")
    sql = "\n".join(body).strip()
    if not sql or len(sql.encode("utf-8")) > MAX_QUERY_BYTES:
        reject("SCHEMA", "Warehouse query body is empty or exceeds its reviewed bound.")
    queries[name] = {
        "id": name,
        "metrics": tuple(metrics),
        "sql": sql,
        "checksum": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
    }


def load_queries(raw_text: str | None = None) -> dict:
    """Parse the packaged named query file; headers never leak into returned SQL."""
    queries: dict = {}
    name, metrics, body = None, None, []
    for line in _read_resource(raw_text).splitlines():
        stripped = line.strip()
        if stripped.startswith("-- name:"):
            _finish_query(queries, name, metrics, body)
            name, metrics, body = stripped[len("-- name:"):].strip(), None, []
        elif stripped.startswith("-- metrics:") and name is not None and not any(line.strip() for line in body):
            if metrics is not None:
                reject("SCHEMA", "Duplicate metric header in warehouse query file.")
            metrics = [token.strip() for token in stripped[len("-- metrics:"):].split(",")]
        elif name is not None:
            body.append(line)
    _finish_query(queries, name, metrics, body)
    if not queries:
        reject("SCHEMA", "Packaged warehouse query source declares no queries.")
    return queries


_QUERIES = load_queries()


def warehouse_queries() -> dict:
    """Return a detached copy of the packaged query store."""
    return copy.deepcopy(_QUERIES)


def warehouse_query_ids() -> tuple[str, ...]:
    return tuple(_QUERIES)


def query_sql(query_id: str) -> str:
    entry = _QUERIES.get(query_id)
    if entry is None:
        reject("COVERAGE", "The requested warehouse query is not packaged.")
    return entry["sql"]


def queries_for_metric(metric: str) -> dict:
    return {name: copy.deepcopy(entry) for name, entry in _QUERIES.items() if metric in entry["metrics"]}


def warehouse_output(metric: str | None = None, query_id: str | None = None) -> dict:
    """Return the offline source-query response for the CLI."""
    selected = warehouse_queries()
    if query_id is not None:
        if query_id not in selected:
            reject("COVERAGE", "The requested warehouse query is not packaged.")
        selected = {query_id: selected[query_id]}
    if metric is not None:
        selected = {name: entry for name, entry in selected.items() if metric in entry["metrics"]}
    if not selected:
        reject("COVERAGE", "No packaged warehouse query matches the selected metric.")
    return {
        "operation": "warehouse-queries",
        "resource": RESOURCE,
        "query_count": len(selected),
        "queries": [
            {**entry, "metrics": list(entry["metrics"])}
            for entry in selected.values()
        ],
    }

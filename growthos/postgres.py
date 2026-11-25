"""Read-only current monthly reporting. No DDL, publication or historical revisions."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re

from .classification import UNATTRIBUTED_SOURCE_KEYS
from .contracts import FACT_FIELDS, MAX_FACTS, MAX_MONTHS, checksum, month, validate_data, validate_metadata
from .errors import reject
from .evidence import CITY_PAGE_SIZE, status_result, validate_city_offset
from .targets import METRICS, TARGET_DB_FIELDS, targets_from_records

DEFAULT_ENV_FILE: Path | None = Path(__file__).resolve().parents[1] / ".env"
_UNSET = object()


def local_setting(
    key: str,
    path: Path | None,
    *,
    environ: dict | None = None,
) -> str:
    """Read one literal credential from env or a bounded local file without shell/env mutation."""
    env = os.environ if environ is None else environ
    value = env.get(key)
    if value:
        return value
    missing_msg = (
        "Set GROWTHOS_READER_DSN in .env or the environment outside chat to run analysis."
        if key == "GROWTHOS_READER_DSN"
        else "Configure the required database DSN locally; never paste credentials in chat."
    )
    if path is None:
        reject("CONFIGURATION", missing_msg)
    try:
        if path.is_symlink():
            reject("CONFIGURATION", "A regular bounded local settings file is required.")
        if not path.exists():
            reject("CONFIGURATION", missing_msg)
        if not path.is_file() or path.stat().st_size > 65536:
            reject("CONFIGURATION", "A regular bounded local settings file is required.")
        found = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, candidate = line.split("=", 1)
            name, candidate = name.strip(), candidate.strip()
            if candidate[:1] in ("'", '"'):
                if len(candidate) < 2 or candidate[-1] != candidate[0]:
                    reject("CONFIGURATION", "Malformed quoted local setting; details withheld.")
                candidate = candidate[1:-1]
            if name == key:
                found.append(candidate)
        if len(found) != 1 or not found[0]:
            reject("CONFIGURATION", missing_msg)
        return found[0]
    except (OSError, UnicodeError):
        reject("CONFIGURATION", "Local settings could not be read safely; details withheld.")


def assert_reader_permissions(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT bool_and(has_table_privilege(current_user, name, 'SELECT')
                AND NOT has_table_privilege(current_user, name, 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'))
            FROM (VALUES ('irf.facts'), ('irf.month_metadata')) AS reporting(name)
        """)
        if cursor.fetchone() != (True,):
            reject("READ_ONLY_ROLE", "Analysis requires a dedicated read-only login for monthly reporting tables.")


def validate_reader_settings(settings: dict) -> None:
    host = settings.get("host", "")
    if settings.get("sslmode") != "verify-full" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
        reject("TLS", "Reporting connections require a single explicit network hostname and sslmode=verify-full.")


def read_target_month(connection, value: str):
    """Bounded read shared with the owner importer; caller owns connection permissions."""
    selected = month(value)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {','.join(TARGET_DB_FIELDS)} FROM irf.targets WHERE month=%s ORDER BY kind, city_key LIMIT %s",
            (selected, MAX_FACTS + 1),
        )
        records = [dict(zip(TARGET_DB_FIELDS, row)) for row in cursor.fetchmany(MAX_FACTS + 1)]
    for row in records:
        row["month"] = row["month"].isoformat()[:7]
    result = targets_from_records(records)
    if result.data["month"] != value:
        reject("STORE_INTEGRITY", "Stored target month does not match the request.")
    return result


@contextmanager
def connect_reader(*, env_file=_UNSET):
    resolved_env_file = DEFAULT_ENV_FILE if env_file is _UNSET else env_file
    dsn = local_setting("GROWTHOS_READER_DSN", resolved_env_file)
    try:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        reject("DEPENDENCY", "Required reporting database driver is missing; reinstall with python -m pip install -e . from gwos-analyst using the CLI's interpreter.")
    try:
        settings = conninfo_to_dict(dsn)
    except Exception:
        reject("CONFIGURATION", "Invalid reporting connection settings; details withheld.")
    validate_reader_settings(settings)
    try:
        connection = psycopg.connect(dsn, autocommit=True, connect_timeout=10,
            options="-c statement_timeout=15000 -c lock_timeout=2000 -c default_transaction_read_only=on")
    except Exception:
        reject("DATABASE_UNAVAILABLE", "Reporting database connection failed; details withheld.")
    try:
        assert_reader_permissions(connection)
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            yield PostgresStore(connection)
    finally:
        connection.close()


class PostgresStore:
    def __init__(self, connection, *, allow_nonproduction=False):
        self.connection = connection
        self.allow_nonproduction = allow_nonproduction

    def available_months(self) -> list[str]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT month FROM irf.month_metadata ORDER BY month")
            return [row[0].isoformat()[:7] for row in cursor.fetchall()]

    def load_targets(self, value: str):
        month(value)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('irf.targets'), to_regclass('irf.target_imports')")
            if cursor.fetchone() != ('irf.targets', 'irf.target_imports'):
                reject("TARGETS_UNAVAILABLE", "Stored targets are not installed; existing monthly analysis remains available.")
            cursor.execute("""SELECT has_table_privilege(current_user, 'irf.targets', 'SELECT')
                AND NOT has_table_privilege(current_user, 'irf.targets',
                    'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
                AND has_table_privilege(current_user, 'irf.target_imports', 'SELECT')
                AND NOT has_table_privilege(current_user, 'irf.target_imports',
                    'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
                AND NOT has_schema_privilege(current_user, 'irf', 'CREATE')""")
            if cursor.fetchone() != (True,):
                reject("READ_ONLY_ROLE", "Target analysis requires SELECT-only access to stored targets.")
        return read_target_month(self.connection, value)

    def target_months(self, metric: str = "platform_ftu") -> list[str]:
        """Bounded stored target months for one metric; caller enforces table permissions first."""
        if metric not in METRICS:
            reject("ARGUMENTS", "Unsupported target metric.")
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('irf.target_imports')")
            if cursor.fetchone() == (None,):
                return []
            cursor.execute(
                "SELECT month FROM irf.target_imports WHERE metric=%s ORDER BY month LIMIT %s",
                (metric, MAX_MONTHS + 1),
            )
            return [row[0].isoformat()[:7] for row in cursor.fetchmany(MAX_MONTHS + 1)]

    def load(self, months: list[str] | None = None):
        selected = self.available_months()[-2:] if months is None else months
        if not selected or len(selected) > MAX_MONTHS:
            reject("COVERAGE", "Select one to twelve available stored months.")
        dates = [month(m) for m in selected]
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT month, metadata, result_checksum, fact_count FROM irf.month_metadata WHERE month = ANY(%s) ORDER BY month", (dates,))
            stored = cursor.fetchall()
            if [row[0].isoformat()[:7] for row in stored] != sorted(set(selected)) or len(selected) != len(stored):
                reject("COVERAGE", "Requested months are unavailable; no substitute was selected.")
            fields = sorted(FACT_FIELDS)
            cursor.execute(f"SELECT {','.join(fields)} FROM irf.facts WHERE month = ANY(%s) ORDER BY month, city_key LIMIT %s", (dates, MAX_FACTS + 1))
            rows = cursor.fetchmany(MAX_FACTS + 1)
        if len(rows) > MAX_FACTS:
            reject("BOUNDS", "Too many facts for one read; select fewer months.")
        facts = [dict(zip(fields, row)) for row in rows]
        for fact in facts:
            fact["month"] = fact["month"].isoformat()[:7]
        result = validate_data({"nonproduction": self.allow_nonproduction,
            "months": sorted(selected), "month_metadata": [r[1] for r in stored], "facts": facts})
        facts_by_month: dict[str, list[dict]] = {m: [] for m in selected}
        for row in result.data["facts"]:
            facts_by_month[row["month"]].append(row)
        for value, metadata, digest, count in stored:
            m = value.isoformat()[:7]
            current = facts_by_month.get(m, [])
            if metadata["month"] != m or len(current) != count or checksum(current) != digest:
                reject("STORE_INTEGRITY", "Stored month metadata, count or checksum is invalid.")
        return result

    def catalog(self, city_offset=0):
        validate_city_offset(city_offset)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT month, metadata, fact_count FROM irf.month_metadata ORDER BY month")
            stored = cursor.fetchall()
            for value, meta, _ in stored:
                validate_metadata(meta)
                if meta["month"] != value.isoformat()[:7]:
                    reject("STORE_INTEGRITY", "Stored month metadata does not match its key.")
            unattributed = sorted(UNATTRIBUTED_SOURCE_KEYS)
            cursor.execute("SELECT count(DISTINCT city_key) FROM irf.facts WHERE NOT (city_key = ANY(%s))", (unattributed,))
            count = cursor.fetchone()[0]
            cursor.execute(f"""SELECT city_key, city, tier FROM (
                SELECT DISTINCT ON (city_key) city_key, city, tier FROM irf.facts
                WHERE NOT (city_key = ANY(%s))
                ORDER BY city_key, month DESC
            ) AS cities ORDER BY city_key LIMIT {CITY_PAGE_SIZE} OFFSET %s""", (unattributed, city_offset))
            cities = [dict(zip(("city_key", "city", "tier"), row)) for row in cursor.fetchall()]
            cursor.execute("SELECT DISTINCT tier FROM irf.facts WHERE NOT (city_key = ANY(%s)) ORDER BY tier", (unattributed,))
            tiers = [row[0] for row in cursor.fetchall()]
        return status_result(
            months=[r[0].isoformat()[:7] for r in stored],
            month_metadata=[r[1] for r in stored], fact_count=sum(r[2] for r in stored),
            tiers=tiers, city_count=count, cities=cities, city_offset=city_offset,
            nonproduction=self.allow_nonproduction,
        )

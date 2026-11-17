from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from growthos.contracts import FACT_FIELDS, checksum, month, validate_data
from growthos.errors import GrowthOSError
from growthos.postgres import PostgresStore, assert_reader_permissions, connect_reader, local_setting
from fixtures import FixtureReaderStore, demo_data, demo_payload


class ReaderTests(unittest.TestCase):
    def connection(self):
        connection = MagicMock()
        return connection, connection.cursor.return_value.__enter__.return_value

    def test_catalog_matches_fixture_reader_without_loading_facts(self):
        raw = demo_payload()
        for i in range(30):
            raw["facts"].append({**raw["facts"][0], "city_key": f"test-{i:02}", "city": f"Test {i}",
                                 "source_cities": [f"Test {i}"]})
        raw["facts"][-1]["tier"] = "T2"
        dataset = validate_data(raw)
        data = dataset.data
        all_cities = {r["city_key"]: (r["city_key"], r["city"], r["tier"]) for r in data["facts"]}
        cities = [all_cities[k] for k in sorted(all_cities)]
        metadata = [(month(m["month"]), m, sum(r["month"] == m["month"] for r in data["facts"]))
                    for m in data["month_metadata"]]
        for offset in (0, 25, 32, 100, 20_001):
            with self.subTest(offset=offset):
                connection, cursor = self.connection()
                cursor.fetchall.side_effect = [metadata, cities[offset:offset + 25], [("T1",), ("T2",), ("T3",)]]
                cursor.fetchone.return_value = (len(cities),)
                store = PostgresStore(connection, allow_nonproduction=True)
                actual = store.catalog(offset)
                self.assertEqual(actual, FixtureReaderStore(dataset).catalog(offset))
                self.assertEqual(set(actual), {
                    "operation", "nonproduction", "available_months", "month_metadata", "fact_count",
                    "metrics", "tiers", "city_count", "cities", "city_offset", "next_city_offset",
                    "omitted_city_count", "narrowing", "warnings",
                })
                self.assertEqual(actual["fact_count"], len(data["facts"]))
                self.assertEqual(len(actual["warnings"]), 2)
                self.assertNotIn("result_checksum", actual)
                self.assertLessEqual(len(actual["cities"]), 25)
                calls = cursor.execute.call_args_list
                self.assertEqual(len(calls), 4)
                self.assertIn("DISTINCT ON (city_key)", calls[2].args[0])
                self.assertIn("ORDER BY city_key, month DESC", calls[2].args[0])
                self.assertIn("LIMIT 25 OFFSET %s", calls[2].args[0])
                self.assertEqual(calls[2].args[1], (offset,))
                cursor.fetchmany.assert_not_called()

    def test_catalog_rejects_bad_offset_and_corrupt_metadata(self):
        for offset in (-1, True, 2**31, "0"):
            connection, cursor = self.connection()
            with self.subTest(offset=offset), self.assertRaises(GrowthOSError):
                PostgresStore(connection).catalog(offset)
            cursor.execute.assert_not_called()
            with self.assertRaises(GrowthOSError):
                FixtureReaderStore(demo_data()).catalog(offset)
        connection, cursor = self.connection()
        cursor.fetchall.return_value = [(date(2026, 7, 1), demo_payload()["month_metadata"][0], 1)]
        with self.assertRaises(GrowthOSError) as caught:
            PostgresStore(connection, allow_nonproduction=True).catalog()
        self.assertEqual(caught.exception.code, "STORE_INTEGRITY")

    def prepare_load(self):
        data = demo_data().data
        selected = data["months"][-2:]
        facts = [r for r in data["facts"] if r["month"] in selected]
        stored = [(month(m["month"]), m, checksum([r for r in facts if r["month"] == m["month"]]),
                   sum(r["month"] == m["month"] for r in facts))
                  for m in data["month_metadata"] if m["month"] in selected]
        connection, cursor = self.connection()
        cursor.fetchall.side_effect = [[(month(m),) for m in data["months"]], stored]
        fields = sorted(FACT_FIELDS)
        cursor.fetchmany.return_value = [tuple(month(r[k]) if k == "month" else r[k] for k in fields) for r in facts]
        return connection, cursor, stored

    def test_load_is_bounded_and_matches_fixture_reader(self):
        connection, cursor, _ = self.prepare_load()
        with patch("growthos.contracts.json.loads", wraps=json.loads) as loads_spy:
            result = PostgresStore(connection, allow_nonproduction=True).load()
            self.assertEqual(loads_spy.call_count, 1)
        self.assertEqual(result, FixtureReaderStore(demo_data()).load())
        self.assertEqual(result.data["months"], ["2026-09", "2026-10"])
        query, params = cursor.execute.call_args.args
        self.assertIn("WHERE month = ANY(%s)", query)
        self.assertIn("LIMIT %s", query)
        self.assertEqual(params, ([date(2026, 9, 1), date(2026, 10, 1)], 20_001))
        cursor.fetchmany.assert_called_once_with(20_001)

    def test_load_rejects_bad_checksums_counts_and_missing_months(self):
        for fault in ("checksum", "count", "missing_month"):
            connection, cursor, stored = self.prepare_load()
            if fault == "missing_month":
                stored.pop()
            else:
                value, metadata, digest, count = stored[0]
                stored[0] = (value, metadata, "invalid" if fault == "checksum" else digest,
                             count + 1 if fault == "count" else count)
            with self.subTest(fault=fault), self.assertRaises(GrowthOSError) as caught:
                PostgresStore(connection, allow_nonproduction=True).load()
            self.assertEqual(caught.exception.code, "COVERAGE" if fault == "missing_month" else "STORE_INTEGRITY")

    def test_permission_check_rejects_nonreader(self):
        connection, cursor = self.connection()
        for result in ((False,), (None,)):
            cursor.fetchone.return_value = result
            with self.assertRaises(GrowthOSError) as caught:
                assert_reader_permissions(connection)
            self.assertEqual(caught.exception.code, "READ_ONLY_ROLE")

    def test_connection_keeps_read_only_transaction_and_closes_on_failure(self):
        connection, cursor = self.connection()
        cursor.fetchone.return_value = (True,)
        psycopg = MagicMock()
        psycopg.connect.return_value = connection
        psycopg.conninfo.conninfo_to_dict.return_value = {"host": "example.invalid", "sslmode": "verify-full"}
        with patch.dict(os.environ, {"GROWTHOS_READER_DSN": "synthetic-dsn"}, clear=True), \
                patch.dict("sys.modules", {"psycopg": psycopg, "psycopg.conninfo": psycopg.conninfo}):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                with connect_reader() as store:
                    self.assertIs(store.connection, connection)
                    raise RuntimeError("synthetic failure")
            self.assertIn("default_transaction_read_only=on", psycopg.connect.call_args.kwargs["options"])
            connection.execute.assert_called_once_with("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.close.assert_called_once()

    def test_dotenv_fallback_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            env_path = Path(temporary) / ".env"
            env_path.write_text("# reader config\nexport GROWTHOS_READER_DSN='synthetic-file-dsn'\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    local_setting("GROWTHOS_READER_DSN", env_path),
                    "synthetic-file-dsn",
                )
                self.assertNotIn("GROWTHOS_READER_DSN", os.environ)
            with patch.dict(os.environ, {"GROWTHOS_READER_DSN": "env-wins"}, clear=True):
                self.assertEqual(
                    local_setting("GROWTHOS_READER_DSN", env_path),
                    "env-wins",
                )

            connection, cursor = self.connection()
            cursor.fetchone.return_value = (True,)
            psycopg = MagicMock()
            psycopg.connect.return_value = connection
            psycopg.conninfo.conninfo_to_dict.return_value = {"host": "example.invalid", "sslmode": "verify-full"}
            with patch.dict(os.environ, {}, clear=True), \
                    patch.dict("sys.modules", {"psycopg": psycopg, "psycopg.conninfo": psycopg.conninfo}):
                with connect_reader(env_file=env_path) as store:
                    self.assertIs(store.connection, connection)
                self.assertEqual(psycopg.connect.call_args.args[0], "synthetic-file-dsn")

            for bad_content in (
                "GROWTHOS_READER_DSN=one\nGROWTHOS_READER_DSN=two\n",
                "GROWTHOS_READER_DSN='unclosed\n",
                "GROWTHOS_READER_DSN=\n",
            ):
                env_path.write_text(bad_content, encoding="utf-8")
                with patch.dict(os.environ, {}, clear=True), self.assertRaises(GrowthOSError) as caught:
                    local_setting("GROWTHOS_READER_DSN", env_path)
                self.assertEqual(caught.exception.code, "CONFIGURATION")

            link_path = Path(temporary) / "symlink.env"
            link_path.symlink_to(env_path)
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(GrowthOSError) as caught:
                local_setting("GROWTHOS_READER_DSN", link_path)
            self.assertEqual(caught.exception.code, "CONFIGURATION")

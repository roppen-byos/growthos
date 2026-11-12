from pathlib import Path
import tomllib
import unittest

from growthos.metrics import definitions, metric_registry


class PackagingTests(unittest.TestCase):
    def test_live_reader_driver_is_required_without_an_extra(self):
        config = tomllib.loads(
            (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        )
        project = config["project"]
        self.assertIn("psycopg[binary]>=3.2,<4", project["dependencies"])
        self.assertNotIn("postgres", project.get("optional-dependencies", {}))
        package_data = config["tool"]["setuptools"]["package-data"]["growthos"]
        self.assertIn("metric_registry.json", package_data)
        self.assertIn("sql/*.sql", package_data)

    def test_packaged_metric_registry_is_unversioned_and_detached(self):
        store = metric_registry()
        self.assertEqual(set(store), {"metrics", "movements", "glossary_distinctions", "rules"})
        self.assertNotIn("version", store)
        self.assertNotIn("schema_version", store)
        store["metrics"]["installs"]["label"] = "Mutated"
        self.assertEqual(metric_registry()["metrics"]["installs"]["label"], "Installs")
        defs = definitions()
        for metric_id, entry in store["metrics"].items():
            self.assertNotIn("decision_refs", entry)
            self.assertNotIn("decision_refs", defs[metric_id])
        self.assertEqual(defs["platform_ftu"]["subject_unit"], "customers")
        self.assertEqual(defs["installs"]["subject_unit"], "installations")
        self.assertFalse(defs["glossary_distinctions"]["svn"]["supported_in_v1"])
        self.assertFalse(defs["glossary_distinctions"]["rr2net"]["supported_in_v1"])

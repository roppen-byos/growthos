from pathlib import Path
import tomllib
import unittest


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

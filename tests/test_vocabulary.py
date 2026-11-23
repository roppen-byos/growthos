"""Cross-check the shipped metric registry against the shipped glossary.

The registry owns the machine contract; the glossary owns names and meanings.
These checks keep the identity mapping explicit without comparing prose: every
registry identifier must be claimed by exactly one glossary entry, and no
registry alias may reuse another term's avoided name.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GLOSSARY = REPO / "GLOSSARY.md"
REGISTRY = REPO / "growthos" / "metric_registry.json"

TERM = re.compile(r"^\*\*(.+?)\*\*:\s*$")
TAG = re.compile(r"^_Registry_:\s*`([a-z0-9_]+)`\s*$")
AVOID = re.compile(r"^_Avoid_:\s*(.+?)\s*$")
SEPARATOR = re.compile(r"[·/–—-]+")


def normalize(text: str) -> str:
    return " ".join(SEPARATOR.sub(" ", text.replace("`", "")).casefold().split())


def canonical_names(term: str) -> set[str]:
    return {normalize(term), normalize(term.split("(", 1)[0])}


def glossary_entries() -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for line in GLOSSARY.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        heading = TERM.match(stripped)
        if heading:
            current = {"term": heading.group(1), "ids": [], "avoids": []}
            entries.append(current)
            continue
        if current is None:
            continue
        tag = TAG.match(stripped)
        if tag:
            current["ids"].append(tag.group(1))
            continue
        avoid = AVOID.match(stripped)
        if avoid:
            current["avoids"].extend(part.strip() for part in avoid.group(1).split(",") if part.strip())
    return entries


class VocabularyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        cls.entries = glossary_entries()

    def registry_ids(self) -> list[str]:
        return [
            *self.registry["metrics"],
            *self.registry["movements"],
            *self.registry["targets"],
        ]

    def tagged_entries(self) -> dict[str, dict]:
        tagged: dict[str, dict] = {}
        for entry in self.entries:
            self.assertLessEqual(
                len(entry["ids"]), 1,
                f"glossary term owns more than one registry id: {entry['term']}",
            )
            for metric_id in entry["ids"]:
                self.assertNotIn(
                    metric_id, tagged,
                    f"registry id tagged more than once: {metric_id}",
                )
                tagged[metric_id] = entry
        return tagged

    def test_every_registry_id_has_one_glossary_entry(self):
        tagged = self.tagged_entries()
        expected = set(self.registry_ids())
        self.assertEqual(set(tagged), expected)

    def test_aliases_do_not_reuse_another_terms_avoided_name(self):
        tagged = self.tagged_entries()
        avoided: dict[str, set[str]] = {}
        for entry in self.entries:
            owner = normalize(entry["term"])
            for token in entry["avoids"]:
                avoided.setdefault(normalize(token), set()).add(owner)

        for section in ("metrics", "movements", "targets"):
            for metric_id, definition in self.registry[section].items():
                owner_entry = tagged[metric_id]
                owner_names = canonical_names(owner_entry["term"])
                tokens = [definition.get("label", ""), *definition.get("aliases", [])]
                for token in tokens:
                    token_name = normalize(token)
                    if not token_name or token_name in owner_names:
                        continue
                    for avoiding_term in avoided.get(token_name, set()):
                        self.fail(
                            f"{metric_id} uses {token!r}, which the glossary avoids for "
                            f"{avoiding_term!r}"
                        )

    def test_glossary_distinctions_are_unsupported_glossary_terms(self):
        for key, distinction in self.registry["glossary_distinctions"].items():
            self.assertFalse(distinction["supported_in_v1"])
            matches = [entry for entry in self.entries if normalize(key) in normalize(entry["term"])]
            self.assertEqual(len(matches), 1, f"glossary distinction has no unique term: {key}")

    def test_excluded_aliases_are_unique_and_not_used(self):
        excluded = self.registry["excluded_aliases"]
        self.assertEqual(len(excluded), len(set(excluded)))
        self.assertTrue(all(alias == alias.strip().lower() for alias in excluded))
        self.assertTrue({"sftu", "ao"}.issubset(excluded))

        used: set[str] = set()
        for section in ("metrics", "movements", "targets"):
            for metric_id, definition in self.registry[section].items():
                used.add(normalize(metric_id))
                used.add(normalize(definition.get("label", "")))
                used.update(normalize(alias) for alias in definition.get("aliases", []))
        for alias in excluded:
            self.assertNotIn(normalize(alias), used)


if __name__ == "__main__":
    unittest.main()
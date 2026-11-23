"""Self-contained checks for the shipped analyst glossary.

These run after the repository is shipped on its own, so they never reach
outside it: only in-repo links are resolved and only this file's terms are
checked for duplication.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GLOSSARY = REPO / "GLOSSARY.md"
TERM = re.compile(r"^\*\*(.+?)\*\*:\s*$")
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


class GlossaryTest(unittest.TestCase):
    def setUp(self):
        self.text = GLOSSARY.read_text(encoding="utf-8").splitlines()

    def test_terms_are_unique(self):
        seen: set[str] = set()
        for number, line in enumerate(self.text, 1):
            match = TERM.match(line.strip())
            if not match:
                continue
            key = " ".join(match.group(1).replace("`", "").split()).casefold()
            self.assertNotIn(key, seen, f"duplicate term on line {number}: {match.group(1)}")
            seen.add(key)

    def test_internal_links_resolve(self):
        for number, line in enumerate(self.text, 1):
            for target in LINK.findall(line):
                target = target.split("#", 1)[0].strip()
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = (GLOSSARY.parent / target).resolve()
                if REPO not in resolved.parents and resolved != REPO:
                    continue  # workspace-only link, absent after shipping
                self.assertTrue(resolved.exists(), f"broken link on line {number}: {target}")


if __name__ == "__main__":
    unittest.main()
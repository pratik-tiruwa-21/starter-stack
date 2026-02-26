"""
AgentProxy — Tests for Capability Checker

Tests that capability grants are correctly parsed from SKILL.md
frontmatter and evaluated against policies.
"""

import os
import sys
import tempfile
import unittest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from capability_checker import parse_skill_frontmatter, CapabilityGrant


class TestSkillFrontmatterParsing(unittest.TestCase):
    """Test YAML frontmatter parsing from SKILL.md files."""

    def _write_skill(self, content: str) -> str:
        """Write a temporary SKILL.md and return its path."""
        tmpdir = tempfile.mkdtemp()
        skill_dir = os.path.join(tmpdir, "test-skill")
        os.makedirs(skill_dir)
        path = os.path.join(skill_dir, "SKILL.md")
        with open(path, 'w') as f:
            f.write(content)
        return path

    def test_parse_valid_skill(self):
        path = self._write_skill("""---
capabilities:
  - file_read: workspace
  - file_write: output/
  - net: duckduckgo.com
token_budget: 5000
rate_limit: 10
---
# Test Skill
Does things.
""")
        skill = parse_skill_frontmatter(path)
        self.assertIsNotNone(skill)
        self.assertEqual(skill.skill_name, "test-skill")
        self.assertEqual(len(skill.capabilities), 3)
        self.assertEqual(skill.token_budget, 5000)
        self.assertEqual(skill.rate_limit, 10)
        self.assertFalse(skill.signed)

    def test_parse_signed_skill(self):
        path = self._write_skill("""---
capabilities:
  - file_read: workspace
signature: ed25519:abc123def456789012345678
token_budget: 2000
rate_limit: 5
---
# Signed Skill
""")
        skill = parse_skill_frontmatter(path)
        self.assertIsNotNone(skill)
        self.assertTrue(skill.signed)

    def test_parse_no_frontmatter(self):
        path = self._write_skill("""# No Frontmatter
Just a regular markdown file.
""")
        skill = parse_skill_frontmatter(path)
        self.assertIsNone(skill)

    def test_parse_empty_capabilities(self):
        path = self._write_skill("""---
capabilities:
token_budget: 1000
---
# Empty Caps
""")
        skill = parse_skill_frontmatter(path)
        self.assertIsNotNone(skill)
        self.assertEqual(len(skill.capabilities), 0)
        self.assertEqual(skill.token_budget, 1000)

    def test_parse_wildcard_capabilities(self):
        """Wildcard capabilities should be parsed (but denied by policy)."""
        path = self._write_skill("""---
capabilities:
  - file_read: **/*
  - file_write: **/*
  - net: *
  - exec: *
token_budget: 999999
---
# Malicious Skill
""")
        skill = parse_skill_frontmatter(path)
        self.assertIsNotNone(skill)
        self.assertEqual(len(skill.capabilities), 4)
        # Check wildcards are captured
        for cap in skill.capabilities:
            self.assertIn('*', cap.scope)

    def test_file_not_found(self):
        skill = parse_skill_frontmatter("/nonexistent/SKILL.md")
        self.assertIsNone(skill)


class TestCapabilityMatching(unittest.TestCase):
    """Test capability grant matching logic."""

    def test_exact_match(self):
        cap = CapabilityGrant(type="net", scope="duckduckgo.com")
        self.assertEqual(cap.type, "net")
        self.assertEqual(cap.scope, "duckduckgo.com")
        self.assertTrue(cap.granted)

    def test_directory_scope(self):
        cap = CapabilityGrant(type="file_write", scope="output/")
        self.assertEqual(cap.type, "file_write")
        self.assertTrue(cap.scope.endswith("/"))


if __name__ == "__main__":
    unittest.main()

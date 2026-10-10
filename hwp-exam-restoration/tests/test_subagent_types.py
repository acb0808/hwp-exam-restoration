"""Antigravity subagent types: the two role files as agent definitions, and when the engine names them."""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import restoration_single as single
from restoration_author_help import tool_examples

ROLES = ('hwp-restoration-reader', 'hwp-restoration-reviewer')
# Names the host resolved in agent definitions of this skill. An unknown name stops the subagent from starting.
KNOWN_TOOLS = {'view_file', 'write_to_file', 'replace_file_content', 'send_message'}


def frontmatter(name):
    text = (ROOT / 'agents' / f'{name}.md').read_text(encoding='utf-8').replace('\r\n', '\n')
    head = text.split('---\n')[1]
    return head, re.findall(r'^  - (\S+)$', head, re.M)


class RoleDefinitionTests(unittest.TestCase):
    def test_roles_start_without_the_default_prompt_and_reach_mcp(self):
        for name in ROLES:
            head, tools = frontmatter(name)
            self.assertIn(f'name: {name}\n', head)
            self.assertIn('excludeDefaultComponents: true\n', head)   # about 6k tokens of fixed prompt instead of 25k
            self.assertIn('inheritMcp: true\n', head)
            self.assertIn('model: inherit\n', head)                   # a tier name would lower the thinking level
            self.assertNotIn('inheritCustomizations', head)           # would bring skills and rules back into the prompt

    def test_tools_are_names_the_host_resolves(self):
        for name in ROLES:
            _, tools = frontmatter(name)
            self.assertTrue(tools)
            self.assertLessEqual(set(tools), KNOWN_TOOLS)
            self.assertNotIn('call_mcp_tool', tools)                  # "not found in registry": MCP comes from inheritMcp
        self.assertIn('replace_file_content', frontmatter('hwp-restoration-reader')[1])

    def test_each_role_has_one_heading_before_its_instructions(self):
        for name in ROLES:
            body = (ROOT / 'agents' / f'{name}.md').read_text(encoding='utf-8').replace('\r\n', '\n').split('---\n', 2)[2]
            self.assertEqual(len(re.findall(r'^# ', body, re.M)), 1)  # the host cuts the system prompt at top-level headings


class TypeChoiceTests(unittest.TestCase):
    def home_with(self, content):
        home = Path(tempfile.mkdtemp())
        if content is not None:
            target = home / '.gemini/config/agents/hwp-restoration-reader.md'
            target.parent.mkdir(parents=True); target.write_bytes(content)
        return patch.object(single.Path, 'home', return_value=home)

    def test_the_role_type_is_named_only_when_this_skills_definition_is_installed(self):
        current = (ROOT / 'agents/hwp-restoration-reader.md').read_bytes()
        with self.home_with(current):
            self.assertEqual(single.subagent_type('hwp-restoration-reader'), 'hwp-restoration-reader')
        with self.home_with(None):
            self.assertEqual(single.subagent_type('hwp-restoration-reader'), 'self')
        with self.home_with(current.replace(b'inheritMcp: true', b'')):   # an older definition has no MCP access
            self.assertEqual(single.subagent_type('hwp-restoration-reader'), 'self')

    def test_calls_can_be_made_through_the_mcp_dispatcher(self):
        text = tool_examples(Path('C:/job'), 3)
        self.assertIn('call_mcp_tool', text)
        self.assertIn('ServerName `hwp-restoration`', text)
        self.assertIn('hwp-restoration', (ROOT / 'agents/hwp-restoration-reviewer.md').read_text(encoding='utf-8').split('hwp_submit_review(job, reviews)')[1][:300])


if __name__ == '__main__':
    unittest.main()

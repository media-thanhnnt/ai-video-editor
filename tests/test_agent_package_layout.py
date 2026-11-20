"""Regression tests for Phase 2.1 modular agent package layout."""
import unittest
from pathlib import Path
import ai_video_editor.agent as agent
from ai_video_editor.agent.manifest import create_manifest, load_manifest
from ai_video_editor.agent.tool_registry import registry, ToolRegistry

class AgentPackageLayoutTests(unittest.TestCase):
    def test_agent_is_package_not_single_py_file(self):
        self.assertTrue(Path(agent.__file__).name == '__init__.py')
        self.assertTrue(Path(agent.__file__).parent.is_dir())

    def test_public_api_stays_compatible(self):
        self.assertIs(agent.create_manifest, create_manifest)
        self.assertIs(agent.load_manifest, load_manifest)
        self.assertIs(agent.registry, registry)
        self.assertIs(agent.ToolRegistry, ToolRegistry)

    def test_registry_tools_still_available(self):
        self.assertEqual(
            {item['name'] for item in registry().describe()},
            {'create_manifest','bind_asset','approve_manifest','compile_storyboard'}
        )

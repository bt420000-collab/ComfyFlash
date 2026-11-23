from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from xcard import XCardCatalog, XCardError


class XCardCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.xcard_dir = Path(self.temp.name) / "xcard"
        self.xcard_dir.mkdir()

    def make_tool(self, name="Demo-Tool"):
        tool_dir = self.xcard_dir / name
        tool_dir.mkdir()
        (tool_dir / "README.md").write_text("# Tool guide\n", encoding="utf-8")
        (tool_dir / "config.yaml").write_text("mode: test\n", encoding="utf-8")
        (tool_dir / "main.py").write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
        return tool_dir

    def test_empty_directory_is_valid(self):
        result = XCardCatalog(self.xcard_dir).list_tools()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertIn("直接放入 xcard", result["message"])

    def test_discovers_tool_folder_without_manifest(self):
        tool_dir = self.make_tool()
        result = XCardCatalog(self.xcard_dir).list_tools()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["tools"][0]["name"], "Demo-Tool")
        self.assertEqual(Path(result["tools"][0]["path"]), tool_dir)

    def test_discovery_does_not_read_tool_config_or_execute_code(self):
        tool_dir = self.make_tool()
        marker = tool_dir / "executed.txt"
        (tool_dir / "main.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
        result = XCardCatalog(self.xcard_dir).list_tools()
        self.assertEqual(result["count"], 1)
        self.assertFalse(marker.exists())

    def test_discovers_multiple_tools_case_insensitively(self):
        self.make_tool("Demo-Tool")
        self.make_tool("Frame-Helper")
        catalog = XCardCatalog(self.xcard_dir)
        result = catalog.list_tools()
        self.assertEqual([item["name"] for item in result["tools"]], ["Demo-Tool", "Frame-Helper"])
        shown = catalog.show_tool("demo-tool")
        self.assertEqual(shown["name"], "Demo-Tool")

    def test_show_returns_folder_path_and_top_level_entries_only(self):
        tool_dir = self.make_tool()
        (tool_dir / "assets").mkdir()
        shown = XCardCatalog(self.xcard_dir).show_tool("Demo-Tool")
        self.assertEqual(Path(shown["path"]), tool_dir)
        names = {entry["name"] for entry in shown["entries"]}
        self.assertEqual(names, {"README.md", "config.yaml", "main.py", "assets"})

    def test_root_files_are_not_mistaken_for_tools(self):
        (self.xcard_dir / "README.md").write_text("host docs", encoding="utf-8")
        (self.xcard_dir / "catalog.py").write_text("# host discovery", encoding="utf-8")
        result = XCardCatalog(self.xcard_dir).list_tools()
        self.assertEqual(result["count"], 0)

    def test_hidden_and_python_cache_directories_are_ignored(self):
        (self.xcard_dir / ".git").mkdir()
        (self.xcard_dir / "__pycache__").mkdir()
        result = XCardCatalog(self.xcard_dir).list_tools()
        self.assertEqual(result["count"], 0)

    def test_unknown_tool_raises_clear_error(self):
        with self.assertRaisesRegex(XCardError, "找不到 Expansion Card 工具"):
            XCardCatalog(self.xcard_dir).show_tool("missing")

    def test_non_directory_root_is_reported(self):
        file_path = Path(self.temp.name) / "not-a-directory"
        file_path.write_text("x", encoding="utf-8")
        result = XCardCatalog(file_path).list_tools()
        self.assertFalse(result["ok"])
        self.assertIn("不是文件夹", result["invalid"][0]["error"])

    def test_cli_xcard_list_outputs_directory_paths(self):
        tool_dir = self.make_tool()
        repo_root = Path(__file__).resolve().parents[2]
        completed = subprocess.run(
            [sys.executable, "-B", "engine/agent.py", "xcard", "--directory", str(self.xcard_dir), "list"],
            cwd=repo_root, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        payload = __import__("json").loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tools"][0]["name"], "Demo-Tool")
        self.assertEqual(Path(payload["tools"][0]["path"]), tool_dir)

    def test_cli_xcard_show_outputs_directory_and_entries(self):
        self.make_tool()
        repo_root = Path(__file__).resolve().parents[2]
        completed = subprocess.run(
            [sys.executable, "-B", "engine/agent.py", "xcard", "--directory", str(self.xcard_dir), "show", "Demo-Tool"],
            cwd=repo_root, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        payload = __import__("json").loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool"]["name"], "Demo-Tool")
        self.assertTrue(Path(payload["tool"]["path"], "README.md").is_file())

    def test_cli_no_longer_requires_validate_or_manifest(self):
        self.make_tool()
        repo_root = Path(__file__).resolve().parents[2]
        completed = subprocess.run(
            [sys.executable, "-B", "engine/agent.py", "xcard", "--directory", str(self.xcard_dir), "list"],
            cwd=repo_root, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)


if __name__ == "__main__":
    unittest.main()

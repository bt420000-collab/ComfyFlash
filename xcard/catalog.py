"""Side-effect-free discovery of ComfyFlash Expansion Card tool directories.

Each immediate child directory of xcard/ is one tool. The host does not require
or interpret per-tool manifests; the Agent reads the tool's own documentation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XCARD_DIR = ROOT / "xcard"
_IGNORED_DIRS = {".", "..", "__pycache__"}


class XCardError(ValueError):
    """Raised when an Expansion Card tool directory cannot be found."""


class XCardCatalog:
    """Discover tool directories without reading configs or executing code."""

    def __init__(self, directory: Path | str | None = None):
        self.directory = Path(directory).expanduser().resolve() if directory else DEFAULT_XCARD_DIR

    def scan(self) -> dict[str, Any]:
        tools: list[dict[str, str]] = []
        invalid: list[dict[str, str]] = []

        if not self.directory.exists():
            return {"tools": tools, "invalid": invalid, "directory": str(self.directory)}
        if not self.directory.is_dir():
            return {
                "tools": tools,
                "invalid": [{"path": str(self.directory), "error": "XCard 根路径不是文件夹"}],
                "directory": str(self.directory),
            }

        root = self.directory.resolve()
        for child in sorted(self.directory.iterdir(), key=lambda item: item.name.casefold()):
            # Root-level files are host documentation/code, not tools.
            # Hidden folders and Python cache folders are implementation details.
            if not child.is_dir() or child.name.startswith(".") or child.name in _IGNORED_DIRS:
                continue
            try:
                resolved = child.resolve()
                if not resolved.is_relative_to(root):
                    invalid.append({
                        "name": child.name,
                        "path": str(child),
                        "error": "工具目录不能通过符号链接指向 XCard 根目录之外",
                    })
                    continue
                tools.append({"name": child.name, "path": str(resolved)})
            except OSError as exc:
                invalid.append({"name": child.name, "path": str(child), "error": str(exc)})

        return {"tools": tools, "invalid": invalid, "directory": str(self.directory)}

    def list_tools(self) -> dict[str, Any]:
        result = self.scan()
        return {
            "ok": not bool(result["invalid"]),
            "count": len(result["tools"]),
            "tools": result["tools"],
            "invalid": result["invalid"],
            "directory": result["directory"],
            "message": (
                "尚未放入制作工具。将工具文件夹直接放入 xcard/ 即可自动发现。"
                if not result["tools"] and not result["invalid"] else None
            ),
        }

    def show_tool(self, name: str) -> dict[str, Any]:
        result = self.scan()
        wanted = name.casefold()
        for tool in result["tools"]:
            if tool["name"].casefold() == wanted:
                folder = Path(tool["path"])
                entries = []
                try:
                    for item in sorted(folder.iterdir(), key=lambda entry: entry.name.casefold()):
                        entries.append({
                            "name": item.name,
                            "type": "directory" if item.is_dir() else "file",
                        })
                except OSError as exc:
                    raise XCardError(f"无法读取工具目录：{exc}") from exc
                return {"name": tool["name"], "path": tool["path"], "entries": entries}
        if any(Path(item["path"]).name.casefold() == wanted for item in result["invalid"]):
            match = next(item for item in result["invalid"] if Path(item["path"]).name.casefold() == wanted)
            raise XCardError(f"工具目录不可用：{match['error']}")
        raise XCardError(f"找不到 Expansion Card 工具：{name}")

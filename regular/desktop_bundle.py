"""Shared, dependency-free checks for a locally built desktop application."""
from __future__ import annotations

import ast
import json
from pathlib import Path

APP_NAME = "老群友"
BUNDLE_ID = "io.github.the-regular.desktop"
BUILD_SCHEMA = 1


def source_version(root):
    tree = ast.parse((root / "regular/__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(x, ast.Name) and x.id == "__version__" for x in node.targets):
            return str(ast.literal_eval(node.value))
    raise ValueError("Missing source version")


def source_files(root):
    yield from root.glob("*.py")
    yield root / "pyproject.toml"
    for name in (".env.example", "sensitive_nicks.example.txt"):
        template = root / name
        if template.is_file():
            yield template
    # Wheels need no source build tool; source checkouts use it in their cache.
    tool = root / "tools/build_desktop.py"
    if tool.is_file():
        yield tool
    for path in (root / "regular").rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix in (".py", ".png", ".webp", ".json", ".css", ".js", ".mjs"):
            yield path


def bundle_current(root, output):
    root, output = Path(root), Path(output)
    app = output / f"{APP_NAME}.app"
    try:
        stamp = json.loads((output / "desktop-build.json").read_text(encoding="utf-8"))
        expected = {"schema": BUILD_SCHEMA, "source": str(root.resolve()), "version": source_version(root)}
        return (all(stamp.get(key) == value for key, value in expected.items())
                and app.joinpath("Contents/MacOS", APP_NAME).is_file()
                and all(path.stat().st_mtime <= stamp["built_at"] for path in source_files(root)))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False

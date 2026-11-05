#!/usr/bin/env python3
"""Prepare the source checkout's venv, then open its local setup panel."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent.parent


def source_version(root: Path) -> str:
    """Read package metadata without importing the application or its dependencies."""
    tree = ast.parse((root / "regular" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            version = ast.literal_eval(node.value)
            if isinstance(version, str) and version:
                return version
    raise ValueError("源码中缺少版本号，请重新下载完整的源码。")


def launch(root: Path, directory: Path) -> int:
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    marker = environment / ".regular-source-install.json"
    expected = {"source": str(root), "version": source_version(root)}
    created = not python.is_file()
    if created:
        print("首次启动：正在创建本地 Python 环境……", flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    try:
        installed = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        installed = None

    # A pinned production snapshot in PYTHONPATH must not override this source.
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    child_env.pop("PYTHONHOME", None)
    if created or installed != expected:
        print("正在安装或更新依赖（需要联网）；安装完成后再打开面板……", flush=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-e", str(root)],
            cwd=root, env=child_env, check=True,
        )
        marker.write_text(json.dumps(expected, ensure_ascii=False) + "\n", encoding="utf-8")

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"实例目录：{directory}", flush=True)
    print("正在打开本机面板；网页中点击「启动机器人」后才连接 QQ。请保持本终端开启。", flush=True)
    result = subprocess.run(
        [str(python), "-m", "regular", "--directory", str(directory), "panel", "--open"],
        cwd=root, env=child_env,
    )
    return result.returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="为源码创建本地环境并打开老群友的首次配置面板。需要 Python 3.10+；首次安装需要联网。",
    )
    parser.add_argument(
        "--directory", default=None, metavar="PATH",
        help="实例目录（默认 REGULAR_INSTANCE_DIR，未设置时为 ~/.the-regular）",
    )
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或以上。请安装新版 Python 后再启动。", file=sys.stderr)
        return 1
    directory = Path(args.directory or os.environ.get("REGULAR_INSTANCE_DIR") or "~/.the-regular").expanduser().resolve()
    try:
        return launch(ROOT, directory)
    except subprocess.CalledProcessError as error:
        print("依赖安装失败，面板尚未启动。请查看上面的错误后重新运行。", file=sys.stderr)
        return error.returncode or 1
    except (OSError, ValueError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        print("请确认已解压完整源码、安装 Python 3.10+，并有权写入源码和实例目录。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

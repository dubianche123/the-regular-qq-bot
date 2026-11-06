#!/usr/bin/env python3
"""Prepare the source checkout's venv, then open the desktop companion."""
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


def launch(root: Path, directory: Path, *, no_app=False) -> int:
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    marker = environment / ".regular-source-install.json"
    desktop = sys.platform in ("darwin", "win32") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    native_app = sys.platform == "darwin" and not no_app
    expected = {"source": str(root), "version": source_version(root), "desktop": desktop, "native_app": native_app}
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
        print("正在安装或更新依赖（需要联网）；安装完成后再打开老群友……", flush=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-e", str(root) + ("[desktop-build]" if native_app else "[desktop]" if desktop else "")],
            cwd=root, env=child_env, check=True,
        )
        marker.write_text(json.dumps(expected, ensure_ascii=False) + "\n", encoding="utf-8")

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"实例目录：{directory}", flush=True)
    print("正在打开老群友；鼠标靠近人物可打开设置。设置中点击「启动机器人」后才连接 QQ。请保持本终端开启。", flush=True)
    if native_app:
        output = directory / "desktop"
        print("正在准备独立的「老群友」应用；首次构建需要一点时间，之后会复用。", flush=True)
        subprocess.run([str(python), str(root / "tools/build_desktop.py"), "--source", str(root),
                        "--output", str(output)], cwd=root, env=child_env, check=True)
        command = [str(output / "老群友.app/Contents/MacOS/老群友")]
    else:
        command = [str(python), "-m", "regular"]
    result = subprocess.run(
        [*command, "--directory", str(directory), "pet"],
        cwd=root, env=child_env,
    )
    return result.returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="为源码创建本地环境并打开老群友桌宠。需要 Python 3.10+；首次安装需要联网。",
    )
    parser.add_argument(
        "--directory", default=None, metavar="PATH",
        help="实例目录（默认 REGULAR_INSTANCE_DIR，未设置时为 ~/.the-regular）",
    )
    parser.add_argument("--no-app", action="store_true", help="macOS 源码开发：跳过独立应用构建，直接使用 Python 桌宠")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或以上。请安装新版 Python 后再启动。", file=sys.stderr)
        return 1
    directory = Path(args.directory or os.environ.get("REGULAR_INSTANCE_DIR") or "~/.the-regular").expanduser().resolve()
    try:
        return launch(ROOT, directory, no_app=args.no_app)
    except subprocess.CalledProcessError as error:
        print("启动准备失败，老群友尚未打开。请查看上面的安装或应用构建错误后重新运行。", file=sys.stderr)
        return error.returncode or 1
    except (OSError, ValueError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        print("请确认已解压完整源码、安装 Python 3.10+，并有权写入源码和实例目录。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

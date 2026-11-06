#!/usr/bin/env python3
"""Build a real macOS application bundle with PyInstaller, outside the source tree.

Install ``.[desktop-build]`` first. The source launcher calls this on macOS;
normal CLI and headless installations do not need the build dependency.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
from regular.desktop_bundle import APP_NAME, BUNDLE_ID, BUILD_SCHEMA, bundle_current, source_version


def spec_text(root, icon):
    # Hidden imports are declared from the actual flat modules and regular
    # package. No instance .env, keys, chat logs or user files are collected.
    flat = sorted(path.stem for path in root.glob("*.py"))
    data = [(str(root / name), ".") for name in (".env.example", "sensitive_nicks.example.txt")]
    return f'''from PyInstaller.utils.hooks import collect_data_files, collect_submodules
a = Analysis([{str(root / 'regular/desktop_entry.py')!r}],
    pathex=[{str(root)!r}],
    binaries=[], datas=collect_data_files('regular') + {data!r},
    hiddenimports={flat!r} + collect_submodules('regular'),
    hookspath=[], runtime_hooks=[], excludes=['pytest', 'tkinter', 'PyQt5', 'PyQt6', 'PySide2'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
    name={APP_NAME!r}, debug=False, strip=False, upx=False, console=False,
    argv_emulation=False, icon={str(icon)!r})
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name={APP_NAME!r})
app = BUNDLE(coll, name={APP_NAME + '.app'!r}, icon={str(icon)!r},
    bundle_identifier={BUNDLE_ID!r},
    info_plist={{'CFBundleName': {APP_NAME!r}, 'CFBundleDisplayName': {APP_NAME!r},
                 'CFBundleShortVersionString': {source_version(root)!r},
                 'NSMicrophoneUsageDescription': '语音对话期间，让老群友持续听见你的声音；可随时静音或关闭语音。',
                 'NSHighResolutionCapable': True}})
'''


def build(root, output, *, force=False):
    root, output = root.resolve(), output.expanduser().resolve()
    if sys.platform != "darwin":
        raise ValueError("macOS .app 需要在 macOS 构建；其他系统继续使用原生桌宠入口。")
    if bundle_current(root, output) and not force:
        return output / f"{APP_NAME}.app"
    if importlib.util.find_spec("PyInstaller") is None:
        raise ValueError('请先运行 python -m pip install ".[desktop-build]"。')
    icon = root / "regular/assets/app-icon.png"
    if not icon.is_file():
        raise ValueError("源码缺少 app-icon.png，请重新下载完整源码。")
    output.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    version = source_version(root)
    # Work/spec/icon conversion and the duplicate onedir output are disposable.
    # Only the final .app and its small cache stamp remain in the instance.
    with tempfile.TemporaryDirectory(prefix="regular-app-build-") as temp:
        work = Path(temp)
        spec = work / "regular.spec"
        spec.write_text(spec_text(root, icon), encoding="utf-8")
        log = output / "desktop-build.log"
        with log.open("w", encoding="utf-8") as stream:
            subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                            "--distpath", str(work / "dist"), "--workpath", str(work / "work"),
                            str(spec)], cwd=root, stdout=stream, stderr=subprocess.STDOUT, check=True)
        built = work / "dist" / f"{APP_NAME}.app"
        if not built.joinpath("Contents/MacOS", APP_NAME).is_file():
            raise ValueError("构建没有生成有效的老群友应用。")
        target = output / built.name
        staging = output / f".{APP_NAME}.app-building"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(built, staging, symlinks=True)
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
    # Any source edit during compilation makes the cache stale on next launch,
    # rather than claiming a snapshot contains code changed after analysis.
    stamp = {"schema": BUILD_SCHEMA, "source": str(root), "version": version, "built_at": started_at}
    (output / "desktop-build.json").write_text(json.dumps(stamp, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=Path("~/.the-regular/desktop"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        app = build(args.source, args.output, force=args.force)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"老群友应用构建失败（{type(error).__name__}）。请查看输出目录 desktop-build.log。", file=sys.stderr)
        return 1
    print(f"老群友应用已就绪：{app}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Entry for the branded desktop bundle and its fixed CLI subprocesses.

The executable inside a PyInstaller bundle is a bootloader, not a Python
interpreter. Existing panel/bot children deliberately dispatch ``-m regular``
back here; no arbitrary module or command is executed.
"""
from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["-m"]:
        if args[:2] != ["-m", "regular"]:
            print("老群友只支持自己的 regular 命令。", file=sys.stderr)
            return 2
        args = args[2:]
    # Finder supplies no project working directory. Instance data stays outside
    # the read-only .app and is shared with the source launcher.
    if "--directory" not in args and not any(x.startswith("--directory=") for x in args):
        directory = os.environ.get("REGULAR_INSTANCE_DIR") or "~/.the-regular"
        args = ["--directory", str(Path(directory).expanduser()), *args]
    from regular.cli import main as cli_main
    return cli_main(args)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())

"""Generate service definitions from resolved paths; never install/start a service."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import sys

LABEL = "io.github.the-regular"


def _service_path():
    # Background services do not inherit an interactive shell's Node installation.
    # Keep the detected bin directory, not the rest of the developer's environment.
    node = shutil.which("node")
    directories = [str(Path(node).absolute().parent)] if node else []
    directories.extend(("/usr/local/bin", "/usr/bin", "/bin", "/usr/local/sbin", "/usr/sbin", "/sbin"))
    return os.pathsep.join(dict.fromkeys(directories))


def launchd(directory, python):
    directory = Path(directory).expanduser().resolve()
    return {
        "Label": LABEL,
        "ProgramArguments": [str(Path(python).expanduser().absolute()), "-m", "regular", "--directory", str(directory), "run"],
        "WorkingDirectory": str(directory),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PATH": _service_path()},
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "StandardOutPath": str(directory / "data" / "service.log"),
        "StandardErrorPath": str(directory / "data" / "service.log"),
    }


def launchd_nightly(directory, python, *, hour=9, allow_paid=False):
    """每天早上跑一次 `regular nightly --pet`。从源码目录跑：影子回放要用 git 检出受测版本。

    allow_paid 为真时这个任务每天都会把前一天的真实群聊发给回复模型和裁判 ——
    只有生成定义的人显式给了才写进去。
    """
    directory = Path(directory).expanduser().resolve()
    code = Path(__file__).resolve().parent.parent
    arguments = [str(Path(python).expanduser().absolute()), "-m", "regular",
                 "--directory", str(directory), "nightly", "--pet"]
    return {
        "Label": LABEL + ".nightly",
        "ProgramArguments": arguments + (["--allow-paid"] if allow_paid else []),
        "WorkingDirectory": str(code),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PATH": _service_path(),
                                 "PYTHONPATH": str(code)},
        "StartCalendarInterval": {"Hour": int(hour), "Minute": 0},
        "StandardOutPath": str(directory / "data" / "nightly.log"),
        "StandardErrorPath": str(directory / "data" / "nightly.log"),
    }


def _systemd_quote(value):
    # systemd's documented quoted string escaping; no shell or XML substitution.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('\n', '\\n') + '"'


def systemd(directory, python):
    directory = Path(directory).expanduser().resolve()
    command = " ".join(_systemd_quote(v) for v in (Path(python).expanduser().absolute(), "-m", "regular", "--directory", directory, "run"))
    return f"""[Unit]
Description=The Regular QQ companion
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={_systemd_quote(directory)}
ExecStart=:{command}
Environment=PYTHONUNBUFFERED=1
Environment={_systemd_quote('PATH=' + _service_path())}
Restart=on-failure
RestartSec=10
KillSignal=SIGINT
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("launchd", "systemd"))
    parser.add_argument("--directory", default=str(Path.cwd()))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--output", required=True)
    parser.add_argument("--job", choices=("bot", "nightly"), default="bot")
    parser.add_argument("--hour", type=int, default=9)
    parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args(argv)
    if args.job == "nightly" and (args.kind != "launchd" or not 0 <= args.hour <= 23):
        parser.error("the nightly job is generated for launchd only, with --hour between 0 and 23")
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.kind == "launchd":
        (Path(args.directory).expanduser().resolve() / "data").mkdir(parents=True, exist_ok=True)
        definition = (launchd_nightly(args.directory, args.python, hour=args.hour,
                                      allow_paid=args.allow_paid)
                      if args.job == "nightly" else launchd(args.directory, args.python))
        with output.open("wb") as stream:
            plistlib.dump(definition, stream, sort_keys=False)
    else:
        output.write_text(systemd(args.directory, args.python), encoding="utf-8")
    print(f"Generated {output}; not installed or started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

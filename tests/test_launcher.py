"""Source launcher prepares desktop extras, then opens the pet without QQ autostart."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("regular_source_launcher", ROOT / "tools/start_regular.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class SourceLauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="regular launcher ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "source with spaces"
        (self.root / "regular").mkdir(parents=True)
        (self.root / "regular/__init__.py").write_text('__version__ = "0.3.0"\n', encoding="utf-8")
        self.instance = self.base / "my group instance"
        self.python = self.root / ".venv" / ("Scripts/python.exe" if launcher.sys.platform == "win32" else "bin/python")
        self.marker = self.root / ".venv/.regular-source-install.json"

    def create_environment(self, _path):
        self.python.parent.mkdir(parents=True, exist_ok=True)
        self.python.touch()

    def run_launcher(self, args=(), *, run_result=None, env=None, native_app=False):
        with patch.object(launcher, "ROOT", self.root), \
             patch.object(launcher.venv, "EnvBuilder") as builder, \
             patch.object(launcher.subprocess, "run", return_value=run_result or SimpleNamespace(returncode=0)) as run, \
             patch.dict(os.environ, env or {}, clear=True), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            builder.return_value.create.side_effect = self.create_environment
            code = launcher.main([*(() if native_app else ("--no-app",)), *args])
        return code, builder, run

    def test_help_does_not_create_or_launch_anything(self):
        with patch.object(launcher.venv, "EnvBuilder") as builder, \
             patch.object(launcher.subprocess, "run") as run, \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            launcher.main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        builder.assert_not_called()
        run.assert_not_called()
        self.assertFalse((self.root / ".venv").exists())

    @unittest.skipUnless(os.name != "nt" and shutil.which("sh"), "shell entry points require POSIX")
    def test_shell_entry_points_work_in_a_source_path_with_spaces(self):
        (self.root / "tools").mkdir()
        shutil.copyfile(ROOT / "tools/start_regular.py", self.root / "tools/start_regular.py")
        for entry in ("start.sh", "start.command"):
            shutil.copyfile(ROOT / entry, self.root / entry)
        for entry in ("start.sh", "start.command"):
            with self.subTest(entry=entry):
                result = subprocess.run(["sh", str(self.root / entry), "--help"],
                                        cwd=self.base, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--directory", result.stdout)
        self.assertFalse((self.root / ".venv").exists())

    def test_first_start_handles_spaces_and_does_not_reuse_production_home(self):
        code, builder, run = self.run_launcher(
            ["--directory", str(self.instance)],
            env={"REGULAR_HOME": "/old/production", "PYTHONPATH": "/old/release", "PYTHONHOME": "/old/python"},
        )
        self.assertEqual(code, 0)
        builder.return_value.create.assert_called_once_with(self.root / ".venv")
        install, panel = run.call_args_list
        self.assertEqual(install.args[0], [str(self.python), "-m", "pip", "install", "-e", str(self.root) + ("[desktop]" if launcher.sys.platform in ("darwin", "win32") else "")])
        self.assertEqual(panel.args[0], [str(self.python), "-m", "regular", "--directory", str(self.instance), "pet"])
        self.assertEqual(panel.kwargs["cwd"], self.root)
        self.assertNotIn("PYTHONPATH", panel.kwargs["env"])
        self.assertNotIn("PYTHONHOME", panel.kwargs["env"])
        self.assertNotIn("shell", panel.kwargs)
        self.assertTrue(self.instance.is_dir())
        self.assertEqual(json.loads(self.marker.read_text())["version"], "0.3.0")

    def test_linux_headless_skips_qt_but_desktop_profile_installs_it(self):
        with patch.object(launcher.sys, "platform", "linux"):
            _, _, headless = self.run_launcher(["--directory", str(self.instance)])
            self.assertEqual(headless.call_args_list[0].args[0][-1], str(self.root))
            self.assertFalse(json.loads(self.marker.read_text())["desktop"])
            _, _, graphical = self.run_launcher(["--directory", str(self.instance)], env={"DISPLAY": ":1"})
            self.assertEqual(graphical.call_args_list[0].args[0][-1], str(self.root) + "[desktop]")
            self.assertTrue(json.loads(self.marker.read_text())["desktop"])

    def test_same_version_skips_install_but_upgrade_installs(self):
        args = ["--directory", str(self.instance)]
        self.run_launcher(args)
        _, builder, run = self.run_launcher(args)
        builder.return_value.create.assert_not_called()
        self.assertEqual(run.call_count, 1)
        (self.root / "regular/__init__.py").write_text('__version__ = "0.3.1"\n', encoding="utf-8")
        _, _, run = self.run_launcher(args)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(json.loads(self.marker.read_text())["version"], "0.3.1")

    def test_macos_default_builds_and_opens_a_branded_app_in_the_instance(self):
        with patch.object(launcher.sys, "platform", "darwin"):
            code, _, run = self.run_launcher(["--directory", str(self.instance)], native_app=True)
        self.assertEqual(code, 0)
        install, build, opened = run.call_args_list
        self.assertEqual(install.args[0][-1], str(self.root) + "[desktop-build]")
        self.assertEqual(build.args[0], [str(self.python), str(self.root / "tools/build_desktop.py"),
                                       "--source", str(self.root), "--output", str(self.instance / "desktop")])
        self.assertEqual(opened.args[0], [str(self.instance / "desktop/老群友.app/Contents/MacOS/老群友"),
                                         "--directory", str(self.instance), "pet"])
        self.assertTrue(json.loads(self.marker.read_text())["native_app"])

    def test_failed_install_does_not_mark_success_or_start_panel(self):
        failure = subprocess.CalledProcessError(9, ["pip"])
        with patch.object(launcher, "ROOT", self.root), \
             patch.object(launcher.venv, "EnvBuilder") as builder, \
             patch.object(launcher.subprocess, "run", side_effect=failure) as run, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            builder.return_value.create.side_effect = self.create_environment
            code = launcher.main(["--directory", str(self.instance)])
        self.assertEqual(code, 9)
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.instance.exists())

    def test_recreated_environment_reinstalls_even_when_version_marker_survives(self):
        args = ["--directory", str(self.instance)]
        self.run_launcher(args)
        self.python.unlink()
        _, builder, run = self.run_launcher(args)
        builder.return_value.create.assert_called_once()
        self.assertEqual(run.call_count, 2)

    def test_panel_failure_is_not_reported_as_success(self):
        code, _, _ = self.run_launcher(["--directory", str(self.instance)], run_result=SimpleNamespace(returncode=7))
        self.assertEqual(code, 7)

    def test_environment_directory_is_optional_and_default_is_private(self):
        _, _, run = self.run_launcher(env={"REGULAR_INSTANCE_DIR": str(self.instance), "REGULAR_HOME": "/production"})
        self.assertEqual(run.call_args.args[0][4], str(self.instance))
        home_env = {"HOME": self.temp.name, "USERPROFILE": self.temp.name, "REGULAR_HOME": "/production"}
        _, _, run = self.run_launcher(env=home_env)
        self.assertEqual(run.call_args.args[0][4], str(self.base / ".the-regular"))


if __name__ == "__main__":
    unittest.main()

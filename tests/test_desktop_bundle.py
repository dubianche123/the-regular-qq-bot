"""Branded bundle entry and cache boundaries; no app build or GUI startup."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from regular import desktop_entry

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_desktop", ROOT / "tools/build_desktop.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class DesktopBundleTest(unittest.TestCase):
    def test_entry_dispatches_only_the_fixed_regular_module_and_keeps_explicit_instance(self):
        with patch('regular.cli.main', return_value=0) as dispatch:
            self.assertEqual(desktop_entry.main(['-m', 'regular', '--directory', '/instance with spaces', 'panel', '--port', '8765']), 0)
            dispatch.assert_called_once_with(['--directory', '/instance with spaces', 'panel', '--port', '8765'])
        with patch('regular.cli.main') as dispatch:
            self.assertEqual(desktop_entry.main(['-m', 'other']), 2)
            dispatch.assert_not_called()

    def test_finder_entry_uses_private_instance_instead_of_the_app_or_working_directory(self):
        with patch.dict(os.environ, {'REGULAR_INSTANCE_DIR': '/private/instance'}), patch('regular.cli.main', return_value=0) as dispatch:
            self.assertEqual(desktop_entry.main([]), 0)
            dispatch.assert_called_once_with(['--directory', '/private/instance'])

    def test_cache_requires_own_source_version_and_rebuilds_after_source_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'source'; out = Path(folder) / 'instance/desktop'
            (root / 'regular').mkdir(parents=True)
            (root / 'tools').mkdir()
            source = root / 'regular/__init__.py'
            source.write_text('__version__ = "1.0.0"\n')
            (root / 'tools/build_desktop.py').write_text('# builder\n')
            (root / 'pyproject.toml').write_text('')
            exe = out / '老群友.app/Contents/MacOS/老群友'
            exe.parent.mkdir(parents=True); exe.touch()
            stamp = {'schema': 1, 'source': str(root.resolve()), 'version': '1.0.0', 'built_at': time.time() + 1}
            (out / 'desktop-build.json').write_text(json.dumps(stamp))
            self.assertTrue(builder.bundle_current(root, out))
            os.utime(source, (stamp['built_at'] + 2, stamp['built_at'] + 2))
            self.assertFalse(builder.bundle_current(root, out))
            stamp['source'] = '/a different checkout'
            (out / 'desktop-build.json').write_text(json.dumps(stamp))
            self.assertFalse(builder.bundle_current(root, out))

    def test_spec_uses_brand_metadata_and_bundles_only_program_resources(self):
        text = builder.spec_text(ROOT, ROOT / 'regular/assets/app-icon.png')
        self.assertIn("'CFBundleName': '老群友'", text)
        self.assertIn("'CFBundleDisplayName': '老群友'", text)
        self.assertIn("bundle_identifier='io.github.the-regular.desktop'", text)
        self.assertIn("collect_data_files('regular')", text)
        self.assertNotIn("regular-bot/data", text)
        self.assertNotIn(".env'", text)

    def test_editing_a_bundled_default_template_invalidates_the_app_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'source'; out = Path(folder) / 'desktop'
            (root / 'regular').mkdir(parents=True)
            (root / 'regular/__init__.py').write_text('__version__ = "1.0.0"\n')
            (root / 'pyproject.toml').write_text('')
            templates = [root / name for name in ('.env.example', 'sensitive_nicks.example.txt')]
            for template in templates:
                template.write_text('# default template\n')
            exe = out / '老群友.app/Contents/MacOS/老群友'
            exe.parent.mkdir(parents=True); exe.touch()
            built = time.time() + 1
            (out / 'desktop-build.json').write_text(json.dumps({'schema': 1, 'source': str(root.resolve()),
                'version': '1.0.0', 'built_at': built}))
            self.assertTrue(builder.bundle_current(root, out))
            for template in templates:
                with self.subTest(template=template.name):
                    os.utime(template, (built + 2, built + 2))
                    self.assertFalse(builder.bundle_current(root, out))
                    os.utime(template, (built - 1, built - 1))


if __name__ == '__main__':
    unittest.main()

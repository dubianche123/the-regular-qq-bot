"""语音的 QQ 投递：一次原始请求、空回执算不明（何时发语音见 test_media_replies）。"""
import asyncio
import builtins
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from delivery import Sender

class DirectTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_http_request_on_connection_reset(self):
        import voice_output_runtime
        class Request:
            async def __aenter__(self): raise ConnectionResetError('after send')
            async def __aexit__(self, *a): return False
        http=SimpleNamespace(check_session=AsyncMock(), is_sandbox=False,
              _headers={'Authorization':'secret'}, _session=SimpleNamespace(request=Mock(return_value=Request())))
        msg=SimpleNamespace(group_openid='g', id='m',
              timestamp=datetime.now(timezone.utc).isoformat(), _api=SimpleNamespace(_http=http))
        transport=voice_output_runtime.QQTransport(msg,Sender(),lambda: True)
        with self.assertRaises(ConnectionResetError): await transport.send('opaque')
        self.assertEqual(http._session.request.call_count,1)
        self.assertFalse(http._session.request.call_args.kwargs['allow_redirects'])

    async def test_empty_receipt_unknown(self):
        import voice_output_runtime
        from voice_outbound import Delivery
        class Request:
            async def __aenter__(self): return SimpleNamespace(status=200, json=AsyncMock(return_value={}))
            async def __aexit__(self, *a): return False
        http=SimpleNamespace(check_session=AsyncMock(), is_sandbox=False,
              _headers={}, _session=SimpleNamespace(request=Mock(return_value=Request())))
        msg=SimpleNamespace(group_openid='g', id='m',
              timestamp=datetime.now(timezone.utc).isoformat(), _api=SimpleNamespace(_http=http))
        transport=voice_output_runtime.QQTransport(msg,Sender(),lambda: True)
        self.assertIs(await transport.send('opaque'), Delivery.UNKNOWN)
        self.assertEqual(http._session.request.call_count,1)


class ProcessLockTests(unittest.TestCase):
    def test_windows_import_and_fixed_byte_lock_without_fcntl(self):
        """A missing optional POSIX module must not stop even a text-only bot."""
        import voice_output_runtime as runtime
        operations = []
        windows = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0)
        windows.locking = Mock(side_effect=lambda fd, mode, length: operations.append(
            (mode, length, os.lseek(fd, 0, os.SEEK_CUR), os.fstat(fd).st_size)))
        original_import = builtins.__import__

        def import_on_windows(name, *args, **kwargs):
            if name == 'fcntl':
                raise ModuleNotFoundError("No module named 'fcntl'")
            if name == 'msvcrt':
                return windows
            return original_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location('_voice_output_windows', runtime.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch('builtins.__import__', side_effect=import_on_windows):
            spec.loader.exec_module(module)
        self.assertFalse(module.VoiceOutputRuntime(
            SimpleNamespace(VOICE_OUTPUT_ENABLED=False), '').ready())
        with tempfile.TemporaryDirectory() as directory:
            fd = os.open(Path(directory) / 'voice.lock', os.O_CREAT | os.O_RDWR, 0o600)
            try:
                module._lock_file(fd)
                os.lseek(fd, 20, os.SEEK_SET)
                module._unlock_file(fd)
            finally:
                os.close(fd)
        self.assertEqual(operations, [(2, 1, 0, 1), (0, 1, 0, 1)])

    def test_process_lock_excludes_another_process_and_releases(self):
        import voice_output_runtime as runtime
        script = '''import os, sys
from voice_output_runtime import _lock_file, _unlock_file
fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)
try:
    _lock_file(fd)
except OSError:
    os.close(fd)
    sys.exit(17)
_unlock_file(fd)
os.close(fd)
'''

        def child(path):
            return subprocess.run([sys.executable, '-c', script, str(path)],
                cwd=Path(runtime.__file__).resolve().parent,
                capture_output=True, text=True, timeout=15)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'voice.lock'
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                runtime._lock_file(fd)
                blocked = child(path)
                self.assertEqual(blocked.returncode, 17, blocked.stderr)
            finally:
                try:
                    runtime._unlock_file(fd)
                finally:
                    os.close(fd)
            available = child(path)
            self.assertEqual(available.returncode, 0, available.stderr)


class RuntimeLockTests(unittest.IsolatedAsyncioTestCase):
    def config(self, directory):
        node = Path(directory) / 'node'
        node.write_text('')
        node.chmod(0o700)
        silk = Path(directory) / 'silk.cjs'
        silk.write_text('')
        return SimpleNamespace(
            VOICE_OUTPUT_ENABLED=True, SSL_VERIFY=True,
            PROVIDER_PRESETS={'openai': {'base_url': 'https://api.openai.com/v1'}},
            VOICE_OUTPUT_NODE=str(node), VOICE_OUTPUT_SILK_ENTRY=str(silk),
            DATA_DIR=directory, VOICE_OUTPUT_DAILY_LIMIT=10)

    async def test_busy_process_lock_skips_before_billing_or_synthesis(self):
        import voice_output_runtime as runtime
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runtime, '_lock_file', side_effect=BlockingIOError), \
             patch.object(runtime, 'Budget') as budget, \
             patch.object(runtime, 'RealtimeSilkSynth') as synth:
            result = await runtime.VoiceOutputRuntime(self.config(directory), 'synthetic').attempt(
                object(), 'hello', object(), lambda: True)
        self.assertEqual(result, (None, None))
        budget.assert_not_called()
        synth.assert_not_called()

    async def test_cancellation_releases_process_lock(self):
        import voice_output_runtime as runtime
        outgoing = SimpleNamespace(attempt=AsyncMock(side_effect=asyncio.CancelledError))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runtime, 'Budget'), \
             patch.object(runtime, 'RealtimeSilkSynth'), \
             patch.object(runtime, 'VoiceOutbound', return_value=outgoing):
            with self.assertRaises(asyncio.CancelledError):
                await runtime.VoiceOutputRuntime(self.config(directory), 'synthetic').attempt(
                    object(), 'hello', object(), lambda: True)
            fd = os.open(Path(directory) / 'voice-outbound.lock', os.O_RDWR)
            try:
                runtime._lock_file(fd)
                runtime._unlock_file(fd)
            finally:
                os.close(fd)

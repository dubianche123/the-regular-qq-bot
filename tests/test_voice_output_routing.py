"""语音的 QQ 投递：一次原始请求、空回执算不明（何时发语音见 test_media_replies）。"""
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

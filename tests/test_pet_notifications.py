import io

from regular.pet_notifications import cue_for_event, main


def test_hook_only_uses_fixed_event_and_never_forwards_text(capsys):
    sent = []
    payload = '{"hook_event_name":"Stop","last_assistant_message":"secret conversation"}'
    assert main(["--tool", "codex"], stdin=io.StringIO(payload), sender=lambda *args: sent.append(args) or 0) == 0
    assert capsys.readouterr().out == '{}\n'
    assert len(sent) == 1 and "secret" not in str(sent)
    assert cue_for_event("claude", {"hook_event_name": "Notification", "notification_type": "idle_prompt"}) is None
    assert cue_for_event("claude", {"hook_event_name": "Notification", "notification_type": "permission_prompt"})[0] == "think"


def test_bad_event_or_unavailable_pet_does_not_interrupt_hook(capsys):
    assert main(["--tool", "claude"], stdin=io.StringIO('bad'), sender=lambda *args: 1) == 0
    assert capsys.readouterr().out == '{}\n'
    assert main(["--tool", "claude"], stdin=io.StringIO('{"hook_event_name":"Stop"}'), sender=lambda *args: 1) == 0
    assert capsys.readouterr().out == '{}\n'


def test_synthetic_hook_reaches_authenticated_cue(monkeypatch, capsys):
    from types import SimpleNamespace
    from regular import cli, desktop
    requests = []
    monkeypatch.setattr(cli, "load_config", lambda directory: SimpleNamespace(DATA_DIR=directory))
    monkeypatch.setattr(desktop, "_read_token", lambda cfg: "t" * 32)
    monkeypatch.setattr(desktop, "_panel_ready", lambda port, token: True)
    monkeypatch.setattr(desktop.PanelClient, "session", lambda self: {"csrf": "c" * 32})
    monkeypatch.setattr(desktop.PanelClient, "cue", lambda self, action, text, csrf: requests.append((action, text, csrf)) or "queued")
    assert main(["--tool", "claude"], stdin=io.StringIO('{"hook_event_name":"Stop"}')) == 0
    assert requests == [("cheer", "Claude Code 这轮回复结束啦。", "c" * 32)]
    assert capsys.readouterr().out == '{}\n'


def test_installed_cli_accepts_hook_payload_without_a_tools_directory(monkeypatch):
    from regular import cli, pet_notifications
    received = []
    monkeypatch.setattr(cli, "load_config", lambda directory: object())
    monkeypatch.setattr(pet_notifications, "main", lambda args: received.append(args) or 0)
    assert cli.main(["--directory", "/tmp/pet instance", "pet-notify", "--tool", "codex",
                     '{"type":"agent-turn-complete"}']) == 0
    assert received == [["--tool", "codex", "--directory", "/tmp/pet instance", "--port", "8765",
                         '{"type":"agent-turn-complete"}']]


def test_standalone_hook_uses_requested_instance_before_importing_panel(tmp_path):
    import json
    import os
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    import subprocess
    import sys
    import threading
    from urllib.parse import parse_qs

    instance, other = tmp_path / "pet instance", tmp_path / "other project"
    (instance / "data").mkdir(parents=True)
    other.mkdir()
    token = "synthetic-instance-token-0123456789"
    (instance / "data/panel_token").write_text(token)
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body):
            valid = self.headers.get("X-Panel-Token") == token
            self.send_response(200 if valid else 403)
            self.send_header("X-Regular-Panel", "1")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body if valid else {}).encode())

        def do_GET(self):
            self.respond({"available": True, "csrf": "synthetic-csrf-0123456789"})

        def do_POST(self):
            received.append(parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()))
            self.respond({"queued": "synthetic-cue-id"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, REGULAR_HOME=str(other))
        env.pop("DATA_DIR", None)
        result = subprocess.run([sys.executable, str(root / "tools/pet_agent_notify.py"),
                                 "--directory", str(instance), "--port", str(server.server_port),
                                 "--tool", "codex", "--status", "finished"],
                                cwd=other, env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert received == [{"action": ["cheer"], "text": ["Codex 这轮回复结束啦。"],
                             "csrf": ["synthetic-csrf-0123456789"]}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)

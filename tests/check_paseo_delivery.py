"""Check recipient validation, socket acknowledgements, and conversation-scoped reply context."""

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch


def main():
    path = (Path(__file__).resolve().parents[1] / "hermes") / "plugins/paseo-delivery/__init__.py"
    spec = importlib.util.spec_from_file_location("paseo_delivery", path)
    bridge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bridge
    spec.loader.exec_module(bridge)
    agent = "11111111-1111-4111-8111-111111111111"
    config = SimpleNamespace(extra={"agent_id": agent, "provider": "personal-assistant"})
    state = {"Id": agent, "Provider": "personal-assistant", "Status": "idle", "Archived": False}
    registration, hooks, middleware = {}, {}, {}
    bridge.register(SimpleNamespace(register_platform=lambda **kwargs: registration.update(kwargs),
                                    register_cli_command=lambda *args: None,
                                    register_hook=lambda name, fn: hooks.update({name: fn}),
                                    register_middleware=lambda name, fn: middleware.update({name: fn})))
    assert hooks["pre_llm_call"] is bridge.reply_context
    assert middleware["llm_request"] is bridge.apply_reasoning
    assert registration["cron_deliver_env_var"] == "PASEO_HOME_CHANNEL"
    assert registration["is_connected"](config)
    assert not registration["is_connected"](SimpleNamespace(extra={}))
    message = 'literal $(echo unsafe) `command` — report data'
    digest = hashlib.sha256(message.encode()).hexdigest()

    def cli(args, **kwargs):
        assert "PASEO_HOST" not in kwargs["env"] and "PASEO_AGENT_ID" not in kwargs["env"]
        assert kwargs.get("shell", False) is False
        assert args[1] == "inspect", "Publication must never submit a prompt"
        return SimpleNamespace(stdout=json.dumps(state))

    with tempfile.TemporaryDirectory(prefix="paseo-check-", dir="/tmp") as scratch:
        home = Path(scratch)
        lane = home / "paseo"
        lane.mkdir()
        socket_path = lane / f"{agent}.sock"
        record_path = lane / f"{agent}.json"
        with patch.dict(os.environ, {"HERMES_HOME": scratch, "PASEO_HOST": "wrong", "PASEO_AGENT_ID": agent}), \
             patch.object(bridge.subprocess, "run", side_effect=cli) as call:
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(socket_path))
            server.listen()
            def receive():
                with server.accept()[0] as connection:
                    data = bytearray()
                    while b"\n" not in data:
                        data.extend(connection.recv(4096))
                    assert json.loads(data) == {"agent_id": agent, "message": message}
                    connection.sendall(json.dumps({"status": "published", "id": digest}).encode() + b"\n")
            thread = threading.Thread(target=receive)
            thread.start()
            assert asyncio.run(bridge.send(config, agent, message))["delivery_status"] == "published"
            thread.join(timeout=5)
            assert not thread.is_alive()
            server.close()
            socket_path.unlink()
            assert call.call_count == 1
            for field, value in (("Status", "running"), ("Archived", True), ("Provider", "other")):
                previous = state[field]
                state[field] = value
                assert "error" in asyncio.run(bridge.send(config, agent, message))
                state[field] = previous
            call.reset_mock()
            assert "error" in asyncio.run(bridge.send(config, "invalid", message))
            assert "error" in asyncio.run(bridge.send(config, agent, message, media_files=["file"]))
            assert "error" in asyncio.run(bridge.send(config, agent, ""))
            call.assert_not_called()
            assert "error" in asyncio.run(bridge.send(config, agent, message)), "Missing socket must fail visibly"
            assert bridge.reply_context(platform="acp") is None
            record = {"agent_id": agent, "id": digest, "message": message, "published": True}
            record_path.write_text(json.dumps(record))
            assert message in bridge.reply_context(platform="acp")["context"]
            assert bridge.reply_context(platform="other") is None
            for key, value in (("agent_id", "other"), ("id", "wrong"), ("published", False)):
                record_path.write_text(json.dumps({**record, key: value}))
                assert bridge.reply_context(platform="acp") is None
            record_path.write_text("invalid")
            assert bridge.reply_context(platform="acp") is None
        for error in (subprocess.TimeoutExpired("paseo", 20), subprocess.CalledProcessError(1, "paseo"), FileNotFoundError()):
            with patch.object(bridge.subprocess, "run", side_effect=error):
                assert "error" in asyncio.run(bridge.send(config, agent, message))
    print("Paseo destination, direct socket publication, failures, and scoped reply context passed.")


if __name__ == "__main__":
    main()

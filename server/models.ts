// Adapt the native ACP model API for Paseo's typed configuration picker.
export const modelBridge = String.raw`"""Expose native model and thinking choices as ACP configuration options."""

import json
import os
from pathlib import Path
import tempfile
from uuid import UUID
import signal
import subprocess
import sys
import threading


class ModelBridge:
    def __init__(self, home=None):
        self.home = Path(home) if home else None
        self.agent_id = str(UUID(os.environ["PASEO_AGENT_ID"])) if os.environ.get("PASEO_AGENT_ID") else None
        self.reasoning = {}
        self.pending = {}
        self.sessions = {}

    def request(self, message):
        method = message.get("method")
        params = message.get("params", {})
        session = params.get("sessionId")
        selected = None
        reasoning = None
        if method == "session/set_config_option" and params.get("configId") == "hermes.reasoning" and session in self.reasoning:
            option = self.reasoning.get(session)
            if not self.agent_id or not option or params.get("value") not in {o["value"] for o in option["options"]}:
                raise ValueError("Unsupported reasoning level or conversation")
            reasoning = params["value"]
        if (method == "session/set_config_option" and params.get("configId") == "hermes.model"
                and session in self.sessions):
            selected = params["value"]
            message = {**message, "method": "session/set_model",
                       "params": {"sessionId": session, "modelId": selected}}
        if "id" in message and method in {"session/new", "session/load", "session/set_config_option"}:
            self.pending[message["id"]] = (method, session, selected, reasoning)
        return message

    def response(self, message):
        if "method" in message or message.get("id") not in self.pending:
            return message
        method, session, selected, reasoning = self.pending.pop(message["id"])
        if "error" in message:
            return message
        result = message.get("result", {})
        options = result.get("configOptions") or []
        if method in {"session/new", "session/load"}:
            session = result.get("sessionId", session)
            models = result.get("models")
            if models and not any(o.get("category") == "model" for o in options):
                self.sessions[session] = {
                    "id": "hermes.model", "name": "Model", "category": "model", "type": "select",
                    "currentValue": models["currentModelId"],
                    "options": [{"value": m["modelId"], "name": m["name"]}
                                for m in models["availableModels"]],
                }
        if reasoning is not None:
            try:
                directory = self.home / "paseo/reasoning"
                if directory.is_symlink():
                    raise ValueError("Refusing a linked reasoning directory")
                with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        json.dump({"effort": reasoning}, stream)
                        stream.flush()
                        temporary.replace(directory / (self.agent_id + ".json"))
                    finally:
                        temporary.unlink(missing_ok=True)
            except (OSError, ValueError) as error:
                return {"jsonrpc": "2.0", "id": message["id"],
                        "error": {"code": -32603, "message": "Could not save reasoning preference: " + str(error)}}
        if session in self.sessions:
            if selected is not None:
                self.sessions[session] = {**self.sessions[session], "currentValue": selected}
            if not any(o.get("category") == "model" for o in options):
                message = {**message, "result": {**result, "configOptions": [*options, self.sessions[session]]}}
        if self.home and session in self.sessions and not any(o.get("category") == "thought_level" for o in options):
            model = self.sessions[session]["currentValue"]
            try:
                catalog = json.loads((self.home / "paseo/reasoning/catalog.json").read_text())
                entry = catalog.get(model)
                self.reasoning.pop(session, None)
                if entry:
                    effort = entry["default"]
                    if self.agent_id:
                        path = self.home / "paseo/reasoning" / (self.agent_id + ".json")
                        try:
                            if path.is_symlink() or path.stat().st_size > 1024:
                                raise ValueError("Unsafe reasoning preference")
                            saved = json.loads(path.read_text())["effort"]
                            effort = "auto" if saved == "auto" else entry["effective"][saved]
                        except FileNotFoundError:
                            pass
                    self.reasoning[session] = {
                        "id": "hermes.reasoning", "name": "Reasoning", "category": "thought_level", "type": "select",
                        "currentValue": effort,
                        "options": [{"value": "auto", "name": "Profile default"},
                                    *[{"value": level, "name": level.title()} for level in entry["levels"]]],
                    }
                    result = message["result"]
                    message = {**message, "result": {**result, "configOptions": [*result["configOptions"], self.reasoning[session]]}}
            except FileNotFoundError:
                pass
        return message


def main():
    child = subprocess.Popen(sys.argv[2:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    bridge = ModelBridge(sys.argv[1])
    lock = threading.Lock()
    output_lock = threading.Lock()

    def emit(message):
        with output_lock:
            print(json.dumps(message), flush=True)

    def forward_requests():
        try:
            for line in sys.stdin:
                message = json.loads(line)
                try:
                    with lock:
                        message = bridge.request(message)
                except ValueError as error:
                    emit({"jsonrpc": "2.0", "id": message.get("id"),
                          "error": {"code": -32602, "message": str(error)}})
                    continue
                child.stdin.write(json.dumps(message) + "\n")
                child.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            child.stdin.close()

    def stop(_signal=None, _frame=None):
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=forward_requests, daemon=True).start()
    try:
        for line in child.stdout:
            with lock:
                message = bridge.response(json.loads(line))
            emit(message)
    finally:
        stop()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


if __name__ == "__main__":
    main()
`;

"""Check native profile defaults, per-conversation reasoning, and the outgoing payload."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch


def main():
    root = (Path(__file__).resolve().parents[1] / "hermes")
    path = root / "plugins/paseo-delivery/__init__.py"
    spec = importlib.util.spec_from_file_location("paseo_reasoning_check", path)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    source = root.parent / "server/models.ts"
    namespace = {"__name__": "model_bridge_check"}
    exec(compile(source.read_text().split("String.raw`", 1)[1].rsplit("`;", 1)[0], str(source), "exec"), namespace)
    agent = "11111111-1111-4111-8111-111111111111"
    with tempfile.TemporaryDirectory(prefix="paseo-reasoning-", dir="/tmp") as scratch, \
         patch.dict(os.environ, {"HERMES_HOME": scratch, "PASEO_AGENT_ID": agent}):
        home = Path(scratch)
        (home / "config.yaml").write_text("model:\n  default: gpt-5.6-luna\n  provider: openai-codex\nagent:\n  reasoning_effort: max\n  reasoning_overrides:\n    gpt-5.5: low\n")
        plugin.reasoning_catalog()
        bridge = namespace["ModelBridge"](home)
        bridge.request({"id": 1, "method": "session/new", "params": {}})
        response = bridge.response({"id": 1, "result": {"sessionId": "native", "models": {
            "currentModelId": "openai-codex:gpt-5.6-luna", "availableModels": [
                {"modelId": "openai-codex:gpt-5.6-luna", "name": "Luna"},
                {"modelId": "openai-codex:gpt-5.5", "name": "Previous"},
                {"modelId": "opencode:other", "name": "Other"},
            ]}}})
        reasoning = response["result"]["configOptions"][1]
        assert reasoning["category"] == "thought_level" and reasoning["currentValue"] == "max"
        context = {"platform": "acp", "provider": "openai-codex", "api_mode": "codex_responses", "model": "gpt-5.6-luna"}
        request = {"model": "gpt-5.6-luna", "input": [], "reasoning": {"effort": "medium", "summary": "auto"}}
        assert plugin.apply_reasoning(request, **context)["request"]["reasoning"]["effort"] == "max"
        assert plugin.apply_reasoning(request, **{**context, "model": "gpt-5.5"})["request"]["reasoning"]["effort"] == "low"
        preference = home / "paseo/reasoning" / (agent + ".json")
        switch = {"id": 2, "method": "session/set_config_option", "params": {
            "sessionId": "native", "configId": "hermes.reasoning", "value": "low"}}
        bridge.request(switch)
        assert not preference.exists()
        bridge.response({"id": 2, "error": {"code": -32602, "message": "Rejected"}})
        assert not preference.exists(), "Failed acknowledgements must not change actual requests"
        bridge.request({**switch, "id": 3})
        assert bridge.response({"id": 3, "result": {}})["result"]["configOptions"][1]["currentValue"] == "low"
        payload = plugin.apply_reasoning(request, **context)["request"]
        assert payload["reasoning"] == {"effort": "low", "summary": "auto"} and request["reasoning"]["effort"] == "medium"
        assert preference.stat().st_mode & 0o777 == 0o600
        restored = namespace["ModelBridge"](home)
        restored.request({"id": 4, "method": "session/load", "params": {"sessionId": "native"}})
        assert restored.response({"id": 4, "result": {"models": response["result"]["models"]}})["result"]["configOptions"][1]["currentValue"] == "low"
        try:
            bridge.request({**switch, "id": 5, "params": {**switch["params"], "value": "invalid"}})
        except ValueError:
            pass
        else:
            raise AssertionError("Reject unsupported levels")
        preference.write_text(json.dumps({"effort": "max"}))
        assert plugin.apply_reasoning(request, **{**context, "model": "gpt-5.5"})["request"]["reasoning"]["effort"] == "xhigh"
        preference.write_text(json.dumps({"effort": "auto"}))
        assert plugin.apply_reasoning(request, **context)["request"]["reasoning"]["effort"] == "max"
        preference.write_text(json.dumps({"effort": "none"}))
        assert plugin.apply_reasoning(request, **context)["request"]["reasoning"]["effort"] == "none"
        assert plugin.apply_reasoning(request, **{**context, "model": "gpt-6-astra"})["request"]["reasoning"]["effort"] == "low"
        assert plugin.apply_reasoning(request, **{**context, "provider": "other"}) is None
        assert plugin.apply_reasoning(request, **{**context, "platform": "cron"}) is None
        bridge.request({"id": 6, "method": "session/set_config_option", "params": {
            "sessionId": "native", "configId": "hermes.model", "value": "opencode:other"}})
        assert all(o["category"] != "thought_level" for o in bridge.response({"id": 6, "result": {}})["result"]["configOptions"])
    print("Native reasoning defaults, supported choices, acknowledgements, private preferences, restore, provider isolation, and outgoing payload passed.")


if __name__ == "__main__":
    main()

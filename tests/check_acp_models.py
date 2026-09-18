"""Check model discovery, native switching, errors, and typed-ACP passthrough."""

from pathlib import Path


def main():
    source = Path(__file__).resolve().parents[1] / "server/models.ts"
    python = source.read_text().split("String.raw`", 1)[1].rsplit("`;", 1)[0]
    namespace = {"__name__": "model_bridge_check"}
    exec(compile(python, str(source), "exec"), namespace)
    bridge = namespace["ModelBridge"]()
    bridge.request({"id": 1, "method": "session/new", "params": {}})
    response = bridge.response({"id": 1, "result": {
        "sessionId": "native", "modes": {"currentModeId": "default"},
        "models": {"currentModelId": "provider:first", "availableModels": [
            {"modelId": "provider:first", "name": "First"},
            {"modelId": "provider:second", "name": "Second"},
        ]},
    }})
    option = response["result"]["configOptions"][0]
    assert option["category"] == "model" and option["currentValue"] == "provider:first"
    assert option["options"] == [{"value": "provider:first", "name": "First"},
                                 {"value": "provider:second", "name": "Second"}]
    assert response["result"]["modes"] == {"currentModeId": "default"}
    request = {"id": 2, "method": "session/set_config_option", "params": {
        "sessionId": "native", "configId": "hermes.model", "value": "provider:second"}}
    assert bridge.request(request) == {"id": 2, "method": "session/set_model",
                                      "params": {"sessionId": "native", "modelId": "provider:second"}}
    assert bridge.sessions["native"]["currentValue"] == "provider:first", "Change only after acknowledgement"
    error = {"id": 2, "error": {"code": -32602, "message": "Unavailable model"}}
    assert bridge.response(error) == error and bridge.sessions["native"]["currentValue"] == "provider:first"
    bridge.request({**request, "id": 3})
    assert bridge.response({"id": 3, "result": {}})["result"]["configOptions"][0]["currentValue"] == "provider:second"
    bridge.request({"id": 4, "method": "session/load", "params": {"sessionId": "restored"}})
    restored = bridge.response({"id": 4, "result": {"models": response["result"]["models"]}})
    assert restored["result"]["configOptions"][0]["currentValue"] == "provider:first"
    bridge.request({"id": 5, "method": "session/new", "params": {}})
    modern = {"id": 5, "result": {"sessionId": "modern", "models": response["result"]["models"],
                                  "configOptions": [{**option, "id": "native.model"}]}}
    assert bridge.response(modern) == modern and "modern" not in bridge.sessions
    native_request = {**request, "id": 6, "params": {**request["params"], "sessionId": "modern", "configId": "native.model"}}
    assert bridge.request(native_request) == native_request
    notification = {"method": "session/update", "params": {"sessionId": "native", "update": {"sessionUpdate": "agent_message_chunk"}}}
    assert bridge.response(notification) == notification
    print("Native model catalog, switching acknowledgement, errors, restore, and typed-ACP passthrough passed.")


if __name__ == "__main__":
    main()

"""Check JSON choices and validation before any profile or service changes."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock, patch

sys.path.insert(0, str((Path(__file__).resolve().parents[1] / "hermes")))
import install


def main():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "install.json"
        valid = {"profile": "assistant", "agent_name": "Assistant",
                 "settings": {"agent.reasoning_effort": "max", "cron.mirror_delivery": True},
                 "setup": {"pack_location": True, "seed_cron": False}, "gateway": "preserve", "delivery": "local"}
        path.write_text(json.dumps(valid))
        assert install.load_install_config(path) == valid
        with patch.object(install.sys, "argv", ["hermes-pack", "install", "--config", str(path), "--check"]), \
             patch.object(install, "Installer", side_effect=AssertionError("Validation must not touch a profile")), \
             patch.object(install.shutil, "which", side_effect=AssertionError("No Hermes installation required for --check")):
            install.main()
        for invalid in ([], {"unknown": True}, {"setup": {"browser": "yes"}}, {"setup": {"proton": True}},
                        {"profile": "../other"}, {"agent_name": 12}, {"gateway": True}, {"delivery": None}, {"delivery": "target with spaces"},
                        {"settings": {"EXAMPLE_API_KEY": "secret"}},
                        {"settings": {"mcp_servers": {"service": {"env": {"API_KEY": "secret"}}}}},
                        {"settings": {"cron": {}, "cron.mirror_delivery": True}},
                        {"settings": {"agent.system_prompt": "replace identity"}}):
            path.write_text(json.dumps(invalid))
            try:
                install.load_install_config(path)
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid installation JSON accepted")
        for raw in ('{"setup":{"browser":true,"browser":false}}', '{"settings":{"limit":NaN}}',
                    '{"settings":{"limit":1e999}}'):
            path.write_text(raw)
            try:
                install.load_install_config(path)
            except ValueError:
                pass
            else:
                raise AssertionError("Ambiguous or non-finite JSON accepted")
        path.write_text(json.dumps(valid))
        for extra in ([], ["--profile", "other", "--yes"]):
            with patch.object(install.sys, "argv", ["hermes-pack", "install", "--config", str(path), *extra]), \
                 patch.object(install, "Installer") as constructor, contextlib.redirect_stderr(io.StringIO()):
                try:
                    install.main()
                except SystemExit as error:
                    assert error.code == 2
                else:
                    raise AssertionError("Conflicting profile or missing --yes must fail")
                constructor.assert_not_called()
        setup = install.Installer(Path(temporary) / "runtime", "assistant", "hermes")
        setup.unattended = True
        with patch.object(setup, "cli") as cli, patch.object(setup, "register_checkout") as register, \
             patch.object(install, "command") as command, patch.object(install, "ask", side_effect=AssertionError("Unexpected prompt")):
            setup.apply_install_config(valid)
            assert [call.args for call in cli.call_args_list] == [
                ("config", "set", "agent.reasoning_effort", "max"),
                ("config", "set", "cron.mirror_delivery", "true")]
            register.assert_called_once()
            command.assert_not_called(), "False setup choices must not execute or undo anything"
        with patch.object(setup, "config", return_value=None), patch.object(install, "find_chromium", return_value=None), \
             patch.object(install, "choose", side_effect=AssertionError("Browser setup must not prompt")):
            try:
                setup.browser_setup()
            except ValueError as error:
                assert "AGENT_BROWSER_EXECUTABLE_PATH" in str(error)
            else:
                raise AssertionError("Missing browser must fail with an actionable error")
        with patch.object(install.shutil, "which", return_value="paseo"), patch.object(setup, "cli") as cli:
            setup.apply_install_config({"setup": {"paseo": True}})
            cli.assert_called_once_with("skills", "install", "getpaseo/paseo/skills/paseo", "--yes")
        with patch.object(install.shutil, "which", return_value=None), patch.object(setup, "cli") as cli:
            try:
                setup.apply_install_config({"setup": {"paseo": True}})
            except ValueError:
                pass
            else:
                raise AssertionError("Missing Paseo must stop before installing its skill")
            cli.assert_not_called()
        with patch.object(setup, "config", return_value="/missing/browser"), patch.object(install.shutil, "which", return_value=None):
            try:
                setup.browser_setup()
            except ValueError:
                pass
            else:
                raise AssertionError("An invalid explicit browser path must not be silently replaced")
        for gateway, running, expected in (
                ("start", False, [("gateway", "install"), ("gateway", "start"), ("gateway", "status")]),
                ("stop", True, [("gateway", "stop")]),
                ("preserve", True, [("gateway", "stop"), ("gateway", "start")])):
            path.write_text(json.dumps({"profile": "assistant", "gateway": gateway}))
            fake = Mock(home=setup.home, root=setup.root)
            fake.gateway_running.return_value = running
            with patch.object(install.sys, "argv", ["hermes-pack", "install", "--config", str(path), "--yes"]), \
                 patch.object(install, "Installer", return_value=fake), \
                 patch.object(install.shutil, "which", return_value="hermes"), \
                 patch.dict(install.os.environ, {"HERMES_SESSION_ID": ""}):
                install.main()
                assert [call.args for call in fake.cli.call_args_list] == expected
    print("JSON validation, explicit confirmation, native settings, and omitted/false choices passed.")


if __name__ == "__main__":
    main()

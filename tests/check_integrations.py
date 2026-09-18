"""Check Paseo setup and native task routing without live accounts."""

import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hermes"))
import install
import integrations
from integrations.paseo import Paseo


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / ".hermes"
        setup = install.Installer(root, "assistant", "hermes")
        setup.home.mkdir(parents=True)
        (setup.home / "config.yaml").write_text("{}\n")
        jobs = setup.home / "cron/jobs.json"
        jobs.parent.mkdir()
        jobs.write_text(json.dumps({"jobs": [{"id": "starter", "name": "Personal review", "enabled": False}]}))
        previous = jobs.read_bytes()
        settings = {"platforms.paseo.enabled": True,
                    "platforms.paseo.extra.agent_id": "11111111-1111-4111-8111-111111111111",
                    "platforms.paseo.extra.provider": "personal-assistant"}
        with patch.object(setup, "config", side_effect=settings.get):
            assert integrations.destination(setup) == "paseo:" + settings["platforms.paseo.extra.agent_id"]
            assert integrations.status(setup) == "Paseo: Settings saved; replies not tested"
            assert integrations.seed_destination(setup) == "local", "Adopted reviews keep their destinations"
            jobs.write_text(json.dumps({"jobs": []}))
            marker = setup.home / "local/seed-cron.done"
            marker.parent.mkdir()
            marker.touch()
            assert integrations.seed_destination(setup) == "local", "Completed setup keeps task routing"
            marker.unlink()
            jobs.write_bytes(previous)
            settings["platforms.paseo.enabled"] = False
            assert integrations.destination(setup) == "local", "Saved settings must not enable a lane"
            settings["platforms.paseo.enabled"] = True

        paseo_home = Path(temporary) / "paseo"
        paseo_home.mkdir()
        (paseo_home / "hermes-pack.json").write_text(json.dumps([
            {"id": "personal-assistant", "profile": setup.profile, "label": "Assistant"}]))
        executable = Path(temporary) / "paseo-cli"
        executable.touch()
        settings.update({"platforms.paseo.extra.home": str(paseo_home), "platforms.paseo.extra.cli": str(executable),
                         "model.default": "gpt-5.6-luna", "model.provider": "openai-codex"})
        state = {"Id": settings["platforms.paseo.extra.agent_id"], "Provider": "personal-assistant",
                 "Status": "idle", "Archived": False}
        findings = []
        created = []
        def inspect(argv, **kwargs):
            assert not {"PASEO_HOST", "PASEO_AGENT_ID", "PASEO_WORKSPACE_ID"} & kwargs["env"].keys()
            if argv[1] == "ls":
                value = findings
            elif argv[1:3] == ["daemon", "status"]:
                value = {"home": str(paseo_home), "listen": "127.0.0.1:6767", "connectedDaemon": "reachable"}
            elif argv[1].endswith("paseo-create.mjs"):
                options = json.loads(kwargs["input"])
                assert options["provider"] == state["Provider"] and options["model"] == "openai-codex:gpt-5.6-luna"
                assert options["title"] == "Assistant findings" and options["cwd"] == str(setup.home)
                assert "prompt" not in options
                created.append(options)
                findings.append({"id": state["Id"]})
                value = {"agentId": state["Id"]}
            else:
                assert argv[1] == "inspect", "Setup must not submit a prompt"
                value = state
            return install.subprocess.CompletedProcess(argv, 0, json.dumps(value))
        paseo = Paseo(setup, install)
        with patch.object(setup, "config", side_effect=settings.get), \
             patch.object(install.Path, "home", return_value=Path(temporary)), \
             patch.object(install, "choose", side_effect=lambda question, options, default: default), \
             patch.object(install, "ask", side_effect=lambda question, default: default), \
             patch.object(install, "yes", return_value=True), \
             patch.object(integrations.paseo.shutil, "which", return_value="node"), \
             patch.object(install.subprocess, "run", side_effect=inspect), \
             patch.dict(setup.env, PASEO_HOST="wrong", PASEO_AGENT_ID="wrong", PASEO_WORKSPACE_ID="wrong"), \
             patch.object(setup, "cli") as cli:
            before = jobs.read_bytes()
            paseo.setup()
            assert [call.args for call in cli.call_args_list] == [
                ("plugins", "enable", "paseo-delivery"),
                ("config", "set", "platforms.paseo.extra.agent_id", state["Id"]),
                ("config", "set", "platforms.paseo.extra.provider", state["Provider"]),
                ("config", "set", "platforms.paseo.enabled", "true"),
                ("cron", "edit", "starter", "--deliver", "paseo:" + state["Id"])]
            assert setup.failure_destination == "local" and jobs.read_bytes() == before
            paseo.setup()
            assert len(created) == 1, "Repeated setup must reuse the findings conversation"
            cli.reset_mock()
            for key, value in (("Provider", "other"), ("Status", "running"), ("Archived", True)):
                previous = state[key]
                state[key] = value
                try:
                    paseo.setup()
                except ValueError:
                    pass
                else:
                    raise AssertionError("Paseo setup must reject a busy, archived, or unrelated conversation")
                state[key] = previous
            cli.assert_not_called()
            findings.append({"id": "22222222-2222-4222-8222-222222222222"})
            try:
                paseo.setup()
            except ValueError as error:
                assert "Multiple" in str(error)
            else:
                raise AssertionError("Ambiguous findings conversations must not be chosen silently")
            findings.pop()
            cli.assert_not_called()
            with patch.object(install.subprocess, "run", side_effect=install.subprocess.TimeoutExpired("paseo", 20)):
                try:
                    paseo.setup()
                except RuntimeError as error:
                    assert "timed out" in str(error)
                else:
                    raise AssertionError("A failed inspection must stop before saving settings")
            cli.assert_not_called()
            with patch.object(install, "yes", return_value=False):
                paseo.setup()
            cli.assert_not_called()
            with patch.object(install, "yes", side_effect=lambda question, *args, **kwargs: not question.startswith("Send '")):
                paseo.setup()
            cli.assert_not_called(), "Declining task migration must preserve the old connection"
            with patch.object(install, "choose", return_value="keep"):
                setup.failure_destination = None
                integrations.configure_failures(setup, install)
            cli.assert_not_called()
            integrations.route_failures(setup, install, "local")
            cli.assert_called_once_with("cron", "edit", "starter", "--failure-deliver", "local")
            mapping = paseo_home / "hermes-pack.json"
            original = mapping.read_bytes()
            mapping.unlink()
            with patch.object(install, "yes", return_value=True):
                try:
                    paseo.setup()
                except install.StepDeferred as error:
                    assert "Enable trusted plugins" in str(error)
                else:
                    raise AssertionError("Setup must not enable the global plugin trust switch")
            assert not mapping.exists()
            (paseo_home / "config.json").write_text('{"pluginsEnabled":true}')
            state["Provider"] = "hermes-assistant"
            installed = []
            def bootstrap(argv, **kwargs):
                if argv[1:3] == ["plugin", "ls"]:
                    return install.subprocess.CompletedProcess(argv, 0, json.dumps(installed))
                if argv[1:3] == ["plugin", "reload"]:
                    assert argv[3] == "hermes-pack"
                    assert json.loads(mapping.read_text())[0]["id"] == "hermes-assistant"
                    return install.subprocess.CompletedProcess(argv, 0, "")
                if argv[1:3] == ["plugin", "install"]:
                    assert argv[3] == "https://github.com/midodimori/paseo-hermes-pack-plugin.git"
                    assert json.loads(mapping.read_text())[0]["profile"] == setup.profile
                    assert mapping.stat().st_mode & 0o777 == 0o600
                    return install.subprocess.CompletedProcess(argv, 0, "")
                if argv[1] == "ls" and "--label" not in argv:
                    return install.subprocess.CompletedProcess(argv, 0, "[]")
                return inspect(argv, **kwargs)
            with patch.object(install, "yes", return_value=True), \
                 patch.object(install.subprocess, "run", side_effect=bootstrap):
                paseo.setup()
            assert json.loads(mapping.read_text())[0]["id"] == "hermes-assistant"
            mapping.unlink()
            installed.append({"id": "hermes-pack", "source": "git",
                              "remote": "https://github.com/midodimori/paseo-hermes-pack-plugin.git"})
            with patch.object(install, "yes", return_value=True), \
                 patch.object(install.subprocess, "run", side_effect=bootstrap):
                paseo.setup()
            mapping.unlink()
            installed.clear()
            def failed_install(argv, **kwargs):
                if argv[1:3] == ["plugin", "install"]:
                    raise install.subprocess.CalledProcessError(1, argv)
                return bootstrap(argv, **kwargs)
            with patch.object(install, "yes", return_value=True), \
                 patch.object(install.subprocess, "run", side_effect=failed_install):
                try:
                    paseo.setup()
                except install.subprocess.CalledProcessError:
                    pass
                else:
                    raise AssertionError("A failed plugin install must remain retryable")
            assert not mapping.exists(), "Failed registration must restore the private provider map"
            mapping.write_bytes(original)
    print("Paseo setup, private registration, destination validation, and native routing passed.")


if __name__ == "__main__":
    main()

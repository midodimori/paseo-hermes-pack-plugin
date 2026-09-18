"""Check profile lifecycle, private credentials, and setup cancellation without services."""

import contextlib
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys
from unittest.mock import patch


def main():
    sys.path.insert(0, str((Path(__file__).resolve().parents[1] / "hermes")))
    spec = importlib.util.spec_from_file_location("pack_install", (Path(__file__).resolve().parents[1] / "hermes") / "install.py")
    install = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(install)
    prompts = [node for node in ast.walk(ast.parse(Path(install.__file__).read_text()))
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "yes"]
    assert prompts and all({"yes_help", "no_help"} <= {kw.arg for kw in node.keywords} for node in prompts), \
        "Every confirmation must explain both outcomes"
    with tempfile.TemporaryDirectory(prefix="pack-install-check-") as temporary:
        root = Path(temporary)
        setup = install.Installer(root, "assistant", "hermes")
        backups = root / "backups/pack-install"
        backups.mkdir(parents=True)
        previous = backups / "assistant-20260101-120000-000000.zip"
        sibling = backups / "assistant-other-20260101-120000-000000.zip"
        for path in (previous, sibling):
            path.write_bytes(b"previous backup")
        with patch.object(setup, "cli", side_effect=RuntimeError("Backup failed")):
            try:
                setup.backup()
            except RuntimeError:
                pass
        assert previous.exists(), "A failed backup must preserve the previous copy"
        with patch.object(setup, "cli"):
            try:
                setup.backup()
            except RuntimeError:
                pass
        assert previous.exists(), "Missing output must not delete the previous copy"
        def create_backup(*args, **kwargs):
            Path(args[2]).write_bytes(b"successful native backup")
        with patch.object(setup, "cli", side_effect=create_backup):
            latest = setup.backup()
        assert latest.is_file() and not previous.exists()
        assert sibling.exists(), "Retention must not remove another profile's backup"
        setup.home.mkdir(parents=True)
        config = setup.home / "config.yaml"
        config.write_text("local-choice: keep\n")
        (setup.home / "distribution.yaml").write_text("name: assistant\n")
        memory = setup.home / "memories/USER.md"
        memory.parent.mkdir()
        memory.write_text("Existing personal facts\n")
        before = {p.relative_to(setup.home): p.read_bytes() for p in setup.home.rglob("*") if p.is_file()}
        with patch.object(install, "ask", return_value="assistant"):
            assert install.pick_profile(root) == "assistant"
        marker = root / "profiles/.deleted/assistant"
        marker.parent.mkdir()
        marker.write_text("deleted\n")
        try:
            install.pick_profile(root, allow_new=False)
        except install.StepDeferred:
            pass
        else:
            raise AssertionError("Update/removal must not offer a new or deleted profile")
        with patch.object(install, "ask", side_effect=["assistant", "../invalid", "new-profile"]):
            assert install.pick_profile(root) == "new-profile"
            assert not (root / "profiles/new-profile").exists(), "Picking a name must not create a profile"
        marker.unlink()
        orphan = install.Installer(root, "removed", "hermes")
        orphan.home.mkdir()
        (orphan.home / "auth.lock").touch()
        (root / "profiles/.deleted/removed").write_text("deleted\n")
        with patch.object(install, "yes", return_value=True), patch.object(orphan, "cli") as cli:
            orphan.uninstall_profile()
            cli.assert_not_called()
        assert not orphan.home.exists()
        assert list((root / "backups/pack-uninstall").glob("removed-*/auth.lock"))
        # Native cancellation keeps the profile; successful deletion can leave lock files.
        orphan.home.mkdir()
        (orphan.home / "config.yaml").write_text("{}\n")
        with patch.object(install, "yes", return_value=True), patch.object(orphan, "backup"), \
             patch.object(orphan, "cli"):
            try:
                orphan.uninstall_profile()
            except install.StepDeferred:
                pass
            else:
                raise AssertionError("Native cancellation must retain the configured profile")
        def delete_profile(*args, **kwargs):
            (orphan.home / "config.yaml").unlink()
            (orphan.home / "auth.lock").touch()
        with patch.object(install, "yes", return_value=True), patch.object(orphan, "backup"), \
             patch.object(orphan, "cli", side_effect=delete_profile):
            orphan.uninstall_profile()
        assert not orphan.home.exists()
        with patch.object(install, "ask", return_value="1"), patch.object(setup, "cli") as cli:
            setup.profile_setup()
            cli.assert_not_called()
        with patch.object(install, "yes", return_value=False), patch.object(setup, "cli") as cli:
            setup.data_setup()
            cli.assert_not_called()
        assert before == {p.relative_to(setup.home): p.read_bytes() for p in setup.home.rglob("*") if p.is_file()}

        saved = {"platforms.paseo.enabled": True}
        with patch.object(setup, "config", side_effect=saved.get), patch.object(setup, "gateway_running", return_value=True):
            status = setup.setup_status()
            assert status[4] == "Paseo: Conversation or provider missing", "An enabled lane must not hide missing connection details"
            assert status[9] == "No scheduled tasks"
            assert status[10] == "Service running; replies not tested"
            saved.update({"platforms.paseo.extra.agent_id": "11111111-1111-4111-8111-111111111111", "platforms.paseo.extra.provider": "assistant"})
            assert setup.setup_status()[4] == "Paseo: Settings saved; replies not tested"
            for skill in ("paseo", "proton-cli"):
                path = setup.home / "skills" / skill / "SKILL.md"
                path.parent.mkdir(parents=True)
                path.write_text("Instructions\n")
            jobs = setup.home / "cron/jobs.json"
            jobs.parent.mkdir()
            jobs.write_text(json.dumps({"jobs": []}))
            assert setup.setup_status()[9] == "No scheduled tasks", "An empty jobs file is not a schedule"
            jobs.write_text(json.dumps({"jobs": [{"enabled": True}, {"enabled": False}]}))
            status = setup.setup_status()
            assert status[9] == "1 enabled, 1 paused"
            assert "not checked" in status[6] and "not checked" in status[7]
            jobs.unlink()
            jobs.parent.rmdir()

        partial = install.Installer(root, "partial", "hermes")
        partial.home.mkdir()
        (partial.home / "config.yaml").write_text("local-choice: keep\n")
        with patch.object(install, "yes", return_value=False), patch.object(partial, "cli") as cli:
            try:
                partial.profile_setup()
            except install.StepDeferred:
                pass
            else:
                raise AssertionError("Applying a pack to a non-distribution profile needs confirmation")
            cli.assert_not_called()
        with patch.object(install, "yes", return_value=True), \
             patch.object(partial, "backup", side_effect=RuntimeError("Backup failed")), patch.object(partial, "cli") as cli:
            try:
                partial.profile_setup()
            except RuntimeError:
                pass
            else:
                raise AssertionError("Failed backup must block installation into an existing profile")
            cli.assert_not_called()
        def finish_pack(*args, **kwargs):
            assert args[:2] == ("profile", "install"), "Retry must not create the existing profile again"
            (partial.home / "distribution.yaml").write_text("name: partial\n")
        with patch.object(install, "yes", return_value=True), patch.object(partial, "backup") as backup, \
             patch.object(partial, "cli", side_effect=finish_pack):
            partial.profile_setup()
            backup.assert_called_once()

        with patch.object(install, "yes", return_value=True), \
             patch.object(setup, "cli", side_effect=RuntimeError("Backup failed")) as cli:
            try:
                setup.uninstall_profile()
            except RuntimeError:
                pass
            else:
                raise AssertionError("A failed backup must prevent removal")
            assert len(cli.call_args_list) == 1 and cli.call_args.args[0] == "backup"
        assert memory.read_text() == "Existing personal facts\n"

        with patch.object(install, "yes", side_effect=[True, False, False, False, False]), \
             patch.object(setup, "browser_setup"), patch.object(setup, "cli") as cli:
            setup.skills_setup()
            assert cli.call_args_list[0].args == ("skills", "opt-in", "--sync"), "Opt-in must install bundled skills now"

        browser_home = root / "browser-home"
        for version in ("9.0", "10.0", "11.0"):
            browser = browser_home / f".agent-browser/browsers/chrome-{version}/chrome"
            browser.parent.mkdir(parents=True)
            browser.write_text("#!/bin/sh\nexit 0\n")
            browser.chmod(0o600 if version == "11.0" else 0o700)
        expected = browser_home / ".agent-browser/browsers/chrome-10.0/chrome"
        with patch.object(install.Path, "home", return_value=browser_home), \
             patch.object(install.sys, "platform", "linux"), patch.dict(setup.env, PATH=""), \
             patch.object(setup, "config", return_value=None), \
             patch.object(install, "choose", side_effect=lambda q, options, default: default), \
             patch.object(install, "ask") as ask, patch.object(setup, "cli") as cli:
            setup.browser_setup()
            ask.assert_not_called()
            cli.assert_called_once_with("config", "set", "AGENT_BROWSER_EXECUTABLE_PATH", str(expected.resolve()))
            with patch.object(setup, "config", return_value=str(expected)):
                cli.reset_mock()
                setup.browser_setup()
                cli.assert_not_called()
            expected.unlink()
            (browser_home / ".agent-browser/browsers/chrome-9.0/chrome").unlink()
            assert install.find_chromium(setup.env) is None
            setup.browser_setup()  # No executable: default is to skip, never save an invented path.
            cli.assert_not_called()

        shared = root / "bin/browser-use"
        shared.parent.mkdir()
        shared.write_text("#!/bin/sh\nexit 0\n")
        shared.chmod(0o700)
        launcher = setup.home / "bin/browser-use"
        setup.reuse_browser_cli()
        assert launcher.is_symlink() and launcher.resolve() == shared.resolve()
        setup.reuse_browser_cli()
        assert launcher.resolve() == shared.resolve(), "Repeated setup must preserve the launcher"

        with patch.object(install, "yes", return_value=False), patch.object(setup, "cli") as cli:
            try:
                setup.uninstall_profile()
            except install.StepDeferred:
                pass
            else:
                raise AssertionError("Cancelled removal must stop before mutation")
            cli.assert_not_called()
        with patch.object(setup, "backup", side_effect=RuntimeError("Backup failed")), patch.object(setup, "cli") as cli:
            try:
                setup.update_profile()
            except RuntimeError:
                pass
            else:
                raise AssertionError("Failed backup must prevent updating")
            cli.assert_called_once_with("profile", "info", setup.profile, global_command=True)

        state = setup.home / "gateway_state.json"
        state.write_text(json.dumps({"pid": os.getpid()}))
        try:
            setup.require_stopped()
        except ValueError:
            pass
        else:
            raise AssertionError("A live gateway must block profile mutation")
        state.unlink()
        setup.require_stopped()

        secret = "synthetic-private-credential"
        captured = io.StringIO()
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps(secret), stderr="")
        with patch.object(install.subprocess, "run", return_value=result) as run, contextlib.redirect_stdout(captured):
            assert setup.config("EXAMPLE_API_KEY") == secret
        argv = run.call_args.args[0]
        assert secret not in repr(argv) and secret not in captured.getvalue()
        assert argv[:3] == ["hermes", "-p", "assistant"]
        assert run.call_args.kwargs["env"]["HERMES_HOME"] == str(root.resolve())

        from unittest.mock import Mock
        for argv in (["install", "--yes"], ["update", "--yes"], ["uninstall", "--yes"]):
            with patch.object(install.sys, "argv", ["hermes-pack", *argv]), \
                 patch.object(install, "Installer") as constructor, contextlib.redirect_stderr(io.StringIO()):
                try:
                    install.main()
                except SystemExit as error:
                    assert error.code == 2
                else:
                    raise AssertionError("Unattended actions require an explicit profile")
                constructor.assert_not_called()
        with patch.object(setup, "unattended", True), patch.object(install.subprocess, "run", return_value=result) as run:
            setup.cli("profile", "update", setup.profile, global_command=True)
            assert run.call_args.args[0][-1] == "--yes"
            assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL
            setup.cli("config", "get", "model.default", capture=True)
            assert "--yes" not in run.call_args.args[0], "Only supported native commands accept --yes"
        for action in ("install", "update", "uninstall"):
            with patch.object(install, "UI", None), \
                 patch.object(install.sys, "argv", ["hermes-pack", action, "--profile", setup.profile, "--yes"]), \
                 patch.object(install.sys.stdin, "isatty", return_value=False), \
                 patch.object(install.shutil, "which", return_value="hermes"), \
                 patch.object(install, "Installer", return_value=setup), patch.object(setup, "unattended", False), \
                 patch.object(install, "terminal_ui", side_effect=AssertionError("No TUI in unattended mode")), \
                 patch.object(install, "ask", side_effect=AssertionError("No prompts in unattended mode")), \
                 patch.object(setup, "gateway_running", side_effect=[True, False]), \
                 patch.object(setup, "cli") as cli, patch.dict(install.os.environ, {"HERMES_SESSION_ID": ""}), \
                 patch.object(setup, "update_profile") as update, patch.object(setup, "uninstall_profile") as uninstall, \
                 patch.object(setup, "profile_setup") as create, patch.object(setup, "identity_setup") as identity:
                install.main()
                methods = {"install": create, "update": update, "uninstall": uninstall}
                for name, method in methods.items():
                    assert method.call_count == int(name == action)
                assert identity.call_count == int(action == "install")
                assert cli.call_args_list[0].args == ("gateway", "stop")
                assert cli.call_count == (1 if action == "uninstall" else 2)
                if action != "uninstall":
                    assert cli.call_args_list[-1].args == ("gateway", "start")
        with patch.object(install.sys, "argv", ["hermes-pack", "update", "--profile", setup.profile, "--yes"]), \
             patch.object(install.shutil, "which", return_value="hermes"), patch.object(install, "Installer", return_value=setup), \
             patch.object(setup, "unattended", False), patch.object(setup, "gateway_running", return_value=True), \
             patch.object(setup, "cli") as cli, \
             patch.dict(install.os.environ, {"HERMES_SESSION_ID": "active", "HERMES_HOME": str(setup.home)}):
            try:
                install.main()
            except ValueError as error:
                assert "systemd-run" in str(error)
            else:
                raise AssertionError("A gateway task must not stop its own service inline")
            cli.assert_not_called()
        ui = Mock()
        ui.menu.side_effect = ["0", "q"]
        ui.sections.return_value = [3, 4]
        with patch.object(install, "UI", None), patch.object(install, "terminal_ui", return_value=ui), \
             patch.object(install.sys, "argv", ["hermes-pack", "install", "--profile", setup.profile]), \
             patch.object(install.sys.stdin, "isatty", return_value=True), \
             patch.object(install.shutil, "which", return_value="hermes"), \
             patch.object(install, "Installer", return_value=setup), \
             patch.object(setup, "setup_status", return_value={4: "Conversation or provider missing", 5: "Setup options"}), \
             patch.object(setup, "messaging_setup", side_effect=install.StepDeferred("Paseo deferred")), \
             patch.object(setup, "skills_setup") as skills:
            install.main()
            skills.assert_called_once(), "Deferring Paseo must not interrupt other selected setup"
            assert ui.menu.call_args.args[2][4] == "Conversation or provider missing", "Visiting a section must not mark it configured"
        for action in ("update", "uninstall"):
            with patch.object(install, "UI", None), patch.object(install, "terminal_ui", return_value=ui), \
                 patch.object(install.sys, "argv", ["hermes-pack", action, "--profile", setup.profile]), \
                 patch.object(install.sys.stdin, "isatty", return_value=True), \
                 patch.object(install.shutil, "which", return_value="hermes"), \
                 patch.object(install, "Installer", return_value=setup), \
                 patch.object(setup, "update_profile") as update, patch.object(setup, "uninstall_profile") as uninstall, \
                 patch.object(setup, "profile_setup") as create:
                install.main()
                (update if action == "update" else uninstall).assert_called_once()
                (uninstall if action == "update" else update).assert_not_called()
                create.assert_not_called()

        original = "Keep replies brief.\n\n"
        named = install.identity_prompt(original, "Nova")
        assert named.startswith(original) and 'Your name is "Nova".' in named
        assert install.identity_prompt(named, "Nova") == named
        renamed = install.identity_prompt(named, "Étoile")
        assert renamed.startswith(original) and "Nova" not in renamed and "Étoile" in renamed
        for invalid in ("", " ", "name\nnew instructions", "<tag>"):
            try:
                install.identity_prompt(original, invalid)
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid assistant name accepted")
        try:
            install.identity_prompt(named + "\n" + named, "Nova")
        except ValueError:
            pass
        else:
            raise AssertionError("Duplicate identity blocks must not silently overwrite instructions")
        for invalid in ("default", "../other", "UPPER"):
            try:
                install.Installer(root, invalid, "hermes")
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid profile accepted")
    print("Installer preservation, failed/cancelled removal, live-gateway guard, private credentials, and lifecycle checks passed.")


if __name__ == "__main__":
    main()

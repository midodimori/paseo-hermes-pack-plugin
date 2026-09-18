"""Check native installation and update ownership in a temporary Hermes home."""

import os
import json
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch


def main():
    repo = (Path(__file__).resolve().parents[1] / "hermes")
    sys.path.insert(0, str(repo))
    with tempfile.TemporaryDirectory(prefix="nova-distribution-") as temporary:
        scratch = Path(temporary)
        runtime = scratch / "runtime"
        os.environ["HERMES_HOME"] = str(runtime)
        from hermes_constants import get_default_hermes_root
        from hermes_cli.profile_distribution import install_distribution, update_distribution

        assert get_default_hermes_root() == runtime, "Hermes home must be isolated"
        source = scratch / "source"
        # Preserve links: dereferencing them would hide native source-validation failures.
        shutil.copytree(repo, source, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        excluded_diary = source / "local/diary/2026-01-01.md"
        excluded_diary.parent.mkdir(parents=True, exist_ok=True)
        excluded_diary.write_text("Private source record must not ship\n")
        plan = install_distribution(str(source), name="nova")
        target = plan.target_dir
        assert target.is_relative_to(runtime), "Profile escaped the temporary home"
        assert target.name == "nova"
        for name in ("SOUL.md", "config.yaml", "proton-cli.yaml", "templates/USER.md", "scripts/seed-user.py", "scripts/seed-cron.py",
                     "skills/review-context/SKILL.md", "skills/maintain-memory/SKILL.md",
                     "skills/maintain-pack/SKILL.md",
                     "skills/diary-note/SKILL.md", "skills/diary-search/SKILL.md",
                     "skills/diary-forget/SKILL.md", "skills/diary-note/references/diary-format.md",
                     "plugins/paseo-delivery/plugin.yaml", "plugins/paseo-delivery/commands.py"):
            assert (target / name).read_bytes() == (source / name).read_bytes(), name
        for name in ("README.md", "AGENTS.md", "CAPABILITIES.md", "install.py", "installer_ui.py", "hermes-pack", "pyproject.toml", "uv.lock", "tests", "examples", "local", "integrations"):
            assert not (target / name).exists(), f"Repository-only path installed: {name}"

        user = target / "memories/USER.md"
        assert not user.exists(), "Distribution installation must not seed memory automatically"
        import hermes_cli
        entry = str(Path(hermes_cli.__file__).resolve().parent.parent / "hermes")
        helper = subprocess.run(
            [sys.executable, entry, "-p", "nova", "paseo-command", "skills"],
            env={**os.environ, "HERMES_HOME": str(runtime)}, text=True, capture_output=True, check=True,
        )
        names = {command["name"] for command in json.loads(helper.stdout)["commands"]}
        assert "review-context" in names and "maintain-pack" in names, "Installed CLI helper is not registered"
        seed = runpy.run_path(str(target / "scripts/seed-user.py"))["seed_user"]
        template = target / "templates/USER.md"
        assert not template.read_text().strip(), "Public package must not ship personal facts"
        assert not seed(target, template) and not user.exists()
        template.write_text("Uses Example Mail for email.\n")
        expected = template.read_text().strip() + "\n"
        assert len(expected.strip()) <= 1375, "Starting facts exceed the native default user budget"
        assert seed(target, template) and user.read_text() == expected
        assert user.stat().st_mode & 0o777 == 0o600
        assert not seed(target, template), "Repeating setup must preserve existing memory"
        user.write_text("")
        assert seed(target, template), "A zero-byte user file is empty"
        for existing in ("Local preference overrides the template\n", " \n"):
            user.write_text(existing)
            assert not seed(target, template) and user.read_text() == existing
        empty_template = scratch / "empty.md"
        empty_template.write_text("")
        user.write_text("")
        assert not seed(target, empty_template), "Empty starting facts must leave memory alone"
        assert user.read_text() == ""
        outside = scratch / "outside-user.md"
        outside.write_text("Unrelated memory\n")
        user.unlink()
        user.symlink_to(outside)
        try:
            seed(target, template)
        except ValueError:
            pass
        else:
            raise AssertionError("A linked user file must be rejected")
        assert outside.read_text() == "Unrelated memory\n"
        user.unlink()

        # Exercise the installed command and Hermes's reader, not just the helper.
        subprocess.run([sys.executable, str(target / "scripts/seed-user.py"), str(target)], check=True)
        subprocess.run(
            [sys.executable, "-c", "from tools.memory_tool import load_on_disk_store; "
             "from pathlib import Path; from hermes_constants import get_hermes_home; "
             "s = load_on_disk_store(); expected = (get_hermes_home() / 'templates/USER.md').read_text().strip(); "
             "assert s.user_entries == [expected]; assert expected in s._system_prompt_snapshot['user']"],
            env={**os.environ, "HERMES_HOME": str(target)}, check=True,
        )

        subprocess.run(
            [sys.executable, "-c",
             "from unittest.mock import patch; "
             "from hermes_cli.plugins import discover_plugins; discover_plugins(); "
             "from gateway.config import GatewayConfig, Platform, PlatformConfig; "
             "from cron.scheduler_preflight import _preflight_check_delivery; "
             "pc = PlatformConfig(enabled=True, extra={'agent_id': '11111111-1111-4111-8111-111111111111', 'provider': 'personal-assistant'}); "
             "cfg = GatewayConfig(platforms={Platform('paseo'): pc}); "
             "job = {'deliver': 'paseo:11111111-1111-4111-8111-111111111111', 'failure_deliver': 'local'}; "
             "with_patch = patch('gateway.config.load_gateway_config', return_value=cfg); with_patch.start(); "
             "assert _preflight_check_delivery(job) is None; pc.enabled = False; "
             "assert _preflight_check_delivery(job) is not None; with_patch.stop()"],
            env={**os.environ, "HERMES_HOME": str(target)}, check=True,
        )
        from cron.jobs import create_job, remove_job, update_job, use_cron_store

        cron_seed = runpy.run_path(str(target / "scripts/seed-cron.py"))["seed_cron"]
        cli = (sys.executable, "-m", "hermes_cli.main")
        jobs_file = target / "cron/jobs.json"
        marker = target / "local/seed-cron.done"
        assert not jobs_file.exists(), "Installation must not activate schedules"
        assert cron_seed(target, cli)
        jobs = json.loads(jobs_file.read_text())["jobs"]
        assert len(jobs) == 1 and jobs[0]["enabled"]
        job = jobs[0]
        assert job["skills"] == ["review-context"] and job["context_from"] == ["self"]
        assert job["deliver"] == "local" and job["failure_deliver"] == "local"
        assert job["reasoning_effort"] == "medium"
        assert job["schedule"]["kind"] == "interval"
        assert not (runtime / "cron/jobs.json").exists(), "Seed escaped the selected profile"
        before = jobs_file.read_bytes()
        assert not cron_seed(target, cli) and jobs_file.read_bytes() == before
        with use_cron_store(target):
            update_job(job["id"], {"name": "My review", "schedule": "every 6h", "enabled": False})
            before = jobs_file.read_bytes()
            assert not cron_seed(target, cli) and jobs_file.read_bytes() == before
            remove_job(job["id"])
            before = jobs_file.read_bytes()
            assert not cron_seed(target, cli) and jobs_file.read_bytes() == before
            adopted = create_job("Customized review", "every 4h", name="Personal review", paused=True)
        marker.unlink()
        before = jobs_file.read_bytes()
        assert not cron_seed(target, cli) and marker.exists() and jobs_file.read_bytes() == before
        with use_cron_store(target):
            remove_job(adopted["id"])
        marker.unlink()
        for destination in ("local", "paseo:11111111-1111-4111-8111-111111111111"):
            assert cron_seed(target, cli, deliver=destination)
            routed = json.loads(jobs_file.read_text())["jobs"][0]
            assert routed["deliver"] == destination and routed["failure_deliver"] == "local"
            with use_cron_store(target):
                remove_job(routed["id"])
            marker.unlink()
        try:
            cron_seed(target, ("/nonexistent/hermes",))
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("A failed seed must report failure")
        assert not marker.exists(), "A failed seed must remain retryable"

        identity = runpy.run_path(str(source / "install.py"))["identity_prompt"]("Keep replies concise.", "Nova")
        preserved = {
            "config.yaml": "model:\n  default: local-choice\nagent:\n  system_prompt: " + json.dumps(identity) + "\n",
            "memories/USER.md": "Local user preference\n",
            "local/diary/2026-01-01.md": "Private personal history\n",
            "local/pack-repo": "/example/local/checkout\n",
            "local/seed-cron.done": "",
            "cron/jobs.json": jobs_file.read_text(),
            "sessions/example.txt": "Local conversation\n",
            ".env": "EXAMPLE_LOCAL_VALUE=keep\n",
            "vault/example.txt": "Synthetic vault data\n",
            "skills/learned/example/SKILL.md": "Learned skill\n",
            "skills/proton-cli/SKILL.md": "Upstream integration\n",
            "plugins/other/plugin.yaml": "Independent plugin\n",
        }
        for name, content in preserved.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        (source / "SOUL.md").write_text("Updated distribution persona\n")
        update_distribution("nova")
        assert (target / "SOUL.md").read_text() == "Updated distribution persona\n"
        for name, content in preserved.items():
            assert (target / name).read_text() == content, f"Update changed user data: {name}"
        resolved_identity = subprocess.check_output(
            [sys.executable, "-m", "hermes_cli.main", "config", "get", "agent.system_prompt", "--json"],
            env={**os.environ, "HERMES_HOME": str(target)}, text=True,
        )
        assert json.loads(resolved_identity) == identity, "Native configuration lost the chosen assistant identity"
        from hermes_cli.profile_distribution import read_manifest
        assert read_manifest(target).name == "nova", "Update replaced the chosen profile name with the package name"
        assert not seed(target, template) and user.read_text() == preserved["memories/USER.md"]
        update_distribution("nova", force_config=True)
        assert (target / "config.yaml").read_bytes() == (source / "config.yaml").read_bytes()
        assert (target / "local/diary/2026-01-01.md").read_text() == preserved["local/diary/2026-01-01.md"]
        # Exercise a real delete/reinstall: files existing does not guarantee native profile resolution.
        install = runpy.run_path(str(source / "install.py"))
        reset = install["Installer"](runtime, "reset-check", "hermes", "Preview")
        install_distribution(str(source), name=reset.profile)
        old_memory = reset.home / "memories/USER.md"
        old_memory.parent.mkdir(exist_ok=True)
        old_memory.write_text("Old facts\n")

        def native(*args, capture=False, global_command=False):
            prefix = [] if global_command else ["-p", reset.profile]
            result = subprocess.run(
                [sys.executable, "-m", "hermes_cli.main", *prefix, *args],
                env={**os.environ, "HERMES_HOME": str(runtime)}, text=True, capture_output=True,
                input=reset.profile + "\ny\n" if args[:2] == ("profile", "delete") else "y\n",
            )
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            return result.stdout if capture else None

        with patch.object(reset, "cli", side_effect=native), \
             patch.dict(install["Installer"].profile_setup.__globals__,
                        ask=lambda *a: reset.profile, choose=lambda *a: "3", yes=lambda *a, **kw: True):
            reset.update_profile()
            assert old_memory.read_text() == "Old facts\n", "Update must preserve existing memory"
            reset.uninstall_profile()
            assert not reset.home.exists(), "Uninstall must remove the selected profile"
            # Resume after native creation succeeded but distribution installation failed.
            native("profile", "create", reset.profile, "--no-alias", "--no-skills", global_command=True)
            assert not (reset.home / "distribution.yaml").exists()
            note = reset.home / "local/keep.txt"
            note.parent.mkdir(exist_ok=True)
            note.write_text("Keep partial setup data\n")
            reset.profile_setup()
            assert note.read_text() == "Keep partial setup data\n"
            reset.identity_setup()
            assert "Preview" in reset.config("agent.system_prompt")
            assert 'Your name is "Preview".' in (reset.home / "SOUL.md").read_text()
        assert not old_memory.exists(), "Reset must not restore old memory"
        assert list((runtime / "backups/pack-install").glob("reset-check-*.zip")), "Reset needs its private backup"
        # Exercise the actual unattended CLI with closed stdin, not mocked confirmations.
        import hermes_cli
        wrapper = scratch / "hermes-cli"
        native_source = Path(hermes_cli.__file__).resolve().parent.parent
        wrapper.write_text(f'#!/bin/sh\nexport PYTHONPATH={shlex.quote(str(native_source))}\nexec {shlex.quote(sys.executable)} -m hermes_cli.main "$@"\n')
        wrapper.chmod(0o700)
        def unattended(action, *extra):
            result = subprocess.run(
                [sys.executable, str(repo / "install.py"), action, "--profile", "headless",
                 "--root", str(runtime), "--hermes", str(wrapper), "--yes", *extra],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        unattended("install", "--agent-name", "Preview")
        headless = runtime / "profiles/headless"
        facts = headless / "memories/USER.md"
        facts.parent.mkdir(exist_ok=True)
        facts.write_text("Keep my facts\n")
        assert not (headless / "local/seed-cron.done").exists()
        assert not (headless / "cron/jobs.json").exists(), "Install must not activate a schedule"
        unattended("install")
        unattended("update")
        assert facts.read_text() == "Keep my facts\n"
        persona = runpy.run_path(str(repo / "install.py"))["identity_prompt"]
        assert (headless / "SOUL.md").read_text() == persona((repo / "SOUL.md").read_text(), "Preview") + "\n"
        choices = scratch / "install.json"
        choices.write_text(json.dumps({"profile": "headless", "agent_name": "Configured",
            "settings": {"agent.max_turns": 42, "cron.mirror_delivery": False},
            "setup": {"pack_location": True, "seed_user": True, "seed_cron": True}, "gateway": "stop"}))
        unattended("install", "--config", str(choices))
        assert facts.read_text() == "Keep my facts\n", "Explicit seeding must preserve existing memory"
        assert (headless / "local/pack-repo").read_text().strip() == str(repo)
        assert len(json.loads((headless / "cron/jobs.json").read_text())["jobs"]) == 1
        configured = json.loads(subprocess.check_output(
            [str(wrapper), "-p", "headless", "config", "get", "agent.max_turns", "--json"], text=True))
        assert configured == 42
        unattended("update", "--config", str(choices))
        assert len(json.loads((headless / "cron/jobs.json").read_text())["jobs"]) == 1
        assert facts.read_text() == "Keep my facts\n"
        unattended("uninstall")
        assert not headless.exists()
        assert len(list((runtime / "backups/pack-install").glob("headless-*.zip"))) == 1
    print("Native install, initial user facts, cron seeding, plugin validation, and update preservation passed.")


if __name__ == "__main__":
    main()

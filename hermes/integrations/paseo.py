"""Create or reuse a dedicated same-host Paseo conversation for scheduled findings."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import UUID


def install_reference(installer):
    """Install an independent upstream skill through Hermes's normal guard/lifecycle."""
    if not shutil.which("paseo", path=installer.env["PATH"]):
        raise ValueError("Install Paseo and put its CLI on PATH before selecting this setup.")
    installer.cli("skills", "install", "getpaseo/paseo/skills/paseo", "--yes")


class Paseo:
    id = "paseo"
    label = "Paseo"

    def __init__(self, installer, prompts):
        self.installer = installer
        self.prompts = prompts

    def enabled(self):
        return str(self.installer.config("platforms.paseo.enabled")).lower() in ("true", "1", "yes")

    def status(self):
        if not self.installer.config("platforms.paseo.extra.agent_id") or not self.installer.config("platforms.paseo.extra.provider"):
            return "Conversation or provider missing"
        return "Settings saved; replies not tested"

    def destination(self):
        value = self.installer.config("platforms.paseo.extra.agent_id")
        if not self.installer.config("platforms.paseo.extra.provider"):
            raise ValueError("Choose a Paseo provider in Messaging first.")
        try:
            return "paseo:" + str(UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Choose a full Paseo conversation UUID in Messaging first.") from None

    def failure_destination(self):
        print("Paseo keeps routine scheduled-task errors in local logs, without sending a notification.")
        return "local"

    def findings_conversation(self, cli, home, provider, label):
        env = {key: value for key, value in self.installer.env.items()
               if key not in ("PASEO_HOST", "PASEO_AGENT_ID", "PASEO_WORKSPACE_ID")}
        env["PASEO_HOME"] = str(home)

        def call(argv, *, input=None):
            try:
                result = subprocess.run(argv, env=env, input=input, stdin=None if input is not None else subprocess.DEVNULL,
                                        capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Paseo setup timed out; check the daemon before retrying. An already-created findings conversation will be reused.") from None
            if result.returncode:
                raise RuntimeError("Paseo could not prepare the findings conversation; check the daemon and provider before retrying.")
            return json.loads(result.stdout)

        matches = call([str(cli), "ls", "--global", "--label", "hermes-pack.findings=" + self.installer.profile, "--json"])
        if not isinstance(matches, list) or any(not isinstance(entry, dict) or not entry.get("id") for entry in matches):
            raise ValueError("Unexpected Paseo conversation list.")
        if len(matches) > 1:
            raise ValueError("Multiple findings conversations exist for this profile; keep one active in Paseo before retrying.")
        if matches:
            agent = str(UUID(matches[0]["id"]))
        else:
            title = self.prompts.ask("Findings conversation name", label + " findings").strip()
            if not title or len(title) > 200:
                raise ValueError("Use a conversation name between 1 and 200 characters.")
            status = call([str(cli), "daemon", "status", "--json"])
            if status.get("connectedDaemon") != "reachable" or Path(status.get("home", "")).resolve() != home.resolve():
                raise ValueError("Connect to the same-host Paseo daemon before creating the findings conversation.")
            native_model = self.installer.config("model.default")
            native_provider = self.installer.config("model.provider")
            if not isinstance(native_model, str) or not native_model or not isinstance(native_provider, str) or not native_provider:
                raise ValueError("Configure the profile's AI model before creating the findings conversation.")
            node = shutil.which("node", path=env["PATH"])
            if not node:
                raise ValueError("Paseo findings setup needs Node.js on PATH.")
            result = call([node, str(Path(__file__).with_name("paseo-create.mjs")), str(cli)], input=json.dumps({
                "listen": status.get("listen"), "provider": provider,
                "model": native_model if ":" in native_model else native_provider + ":" + native_model,
                "title": title, "profile": self.installer.profile, "cwd": str(self.installer.home),
            }))
            agent = str(UUID(result["agentId"]))
        state = call([str(cli), "inspect", agent, "--json"])
        if not isinstance(state, dict) or state.get("Id") != agent or state.get("Provider") != provider or state.get("Archived") or state.get("Status") != "idle":
            raise ValueError("The findings conversation must be idle, unarchived, and belong to this profile's Paseo provider.")
        return agent

    def setup(self):
        from . import jobs

        cli = Path(self.installer.config("platforms.paseo.extra.cli") or "~/.local/bin/paseo").expanduser()
        home = Path(self.installer.config("platforms.paseo.extra.home") or "~/.paseo").expanduser()
        path = home / "hermes-pack.json"
        if not cli.is_file():
            raise self.prompts.StepDeferred("Install Paseo on this host before configuring Messaging.")
        if home.is_symlink() or path.is_symlink():
            raise ValueError("Refusing a linked Paseo provider configuration.")
        providers = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(providers, list) or any(
                not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]
                or not isinstance(entry.get("profile"), str)
                or (entry.get("label") is not None and not isinstance(entry["label"], str)) for entry in providers):
            raise ValueError("Expected a list of provider IDs and profile names in hermes-pack.json.")
        matching = [entry for entry in providers if entry.get("profile") == self.installer.profile]
        if self.installer.root != (Path.home() / ".hermes").resolve():
            raise ValueError("The Paseo provider currently uses ~/.hermes; choose that Hermes root.")
        if not matching:
            if not self.prompts.yes("Expose this assistant in Paseo and install the trusted plugin from Git?", True,
                                   yes_help="Save a private provider entry and install this repository's unsandboxed Paseo plugin",
                                   no_help="Leave Paseo unchanged; configure messaging later"):
                raise self.prompts.StepDeferred("Paseo connection left for later.")
            settings = home / "config.json"
            if not settings.is_file() or json.loads(settings.read_text()).get("pluginsEnabled") is not True:
                raise self.prompts.StepDeferred("Enable trusted plugins in Paseo Settings → Plugins, then retry Messaging.")
            entry = {"id": "hermes-" + self.installer.profile, "profile": self.installer.profile,
                     "label": self.installer.agent_name or self.installer.profile}
            if any(item["id"] == entry["id"] for item in providers):
                raise ValueError("That provider ID already belongs to another profile; review the private provider map.")
            env = {key: value for key, value in self.installer.env.items()
                   if key not in ("PASEO_HOST", "PASEO_AGENT_ID", "PASEO_WORKSPACE_ID")}
            env["PASEO_HOME"] = str(home)
            result = subprocess.run([str(cli), "ls", "--global", "--json"], env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=True)
            states = json.loads(result.stdout)
            ids = {item["id"] for item in providers}
            if not isinstance(states, list) or any(state.get("provider", "").split("/", 1)[0] in ids
                                                 and state.get("status") == "running" for state in states):
                raise ValueError("Wait for active Hermes conversations to finish before changing its Paseo providers.")
            remote = "https://github.com/midodimori/paseo-hermes-pack-plugin.git"
            result = subprocess.run([str(cli), "plugin", "ls", "--json"], env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=True)
            plugins = json.loads(result.stdout)
            if not isinstance(plugins, list) or any(not isinstance(item, dict) for item in plugins):
                raise ValueError("Unexpected Paseo plugin list.")
            existing = next((item for item in plugins if item.get("id") == "hermes-pack"), None)
            if existing and (existing.get("source") != "git" or existing.get("remote") != remote):
                raise self.prompts.StepDeferred("The hermes-pack plugin ID has another source. Remove its old registration in Paseo, then retry Messaging.")
            action = ["reload", "hermes-pack"] if existing else ["install", remote, "--id", "hermes-pack"]
            original = path.read_bytes() if path.exists() else None
            with tempfile.NamedTemporaryFile(mode="w", dir=home, delete=False) as staged:
                staged.write(json.dumps([*providers, entry], indent=2) + "\n")
                staging = Path(staged.name)
            try:
                os.replace(staging, path)
                subprocess.run([str(cli), "plugin", *action],
                               env=env, stdin=subprocess.DEVNULL, check=True, timeout=180)
            except Exception:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)
                    path.chmod(0o600)
                raise
            finally:
                staging.unlink(missing_ok=True)
            matching = [entry]
        provider = self.prompts.choose("Paseo provider", [(entry["id"], entry.get("label") or entry["id"]) for entry in matching], matching[0]["id"])
        if not self.prompts.yes("Set up a dedicated conversation for scheduled findings?", True,
                               yes_help="Create or reuse a findings conversation; everyday chat stays separate",
                               no_help="Keep the current connection settings"):
            return
        label = next(entry.get("label") or self.installer.profile for entry in matching if entry["id"] == provider)
        agent = self.findings_conversation(cli, home, provider, label)
        target = "paseo:" + agent
        reroute = [job for job in jobs(self.installer)
                   if job.get("deliver") != target and (
                       str(job.get("name", "")).casefold() == "personal review"
                       or str(job.get("deliver", "")).startswith("paseo:"))]
        for job in reroute:
            if not self.prompts.yes(f"Send '{job.get('name') or job['id']}' to the findings conversation?", True,
                                    yes_help="Change only its findings destination; keep its schedule, reasoning, and error routing",
                                    no_help="Keep the current messaging connection and task destinations"):
                return
        if "paseo-delivery" not in (self.installer.config("plugins.enabled") or []):
            self.installer.cli("plugins", "enable", "paseo-delivery")
        for key, value in (("agent_id", agent), ("provider", provider)):
            self.installer.cli("config", "set", "platforms.paseo.extra." + key, value)
        self.installer.cli("config", "set", "platforms.paseo.enabled", "true")
        for job in reroute:
            self.installer.cli("cron", "edit", job["id"], "--deliver", target)
        self.installer.failure_destination = "local"
        print("Paseo findings conversation saved. Other tasks keep their destinations; start the background service when ready.")

"""Install, update, or uninstall a Hermes pack profile on macOS/Linux."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from types import SimpleNamespace

import integrations


REPO = Path(__file__).resolve().parent
UI = None
IDENTITY = re.compile(r"<!-- hermes-pack identity -->\nYour name is (.+)\.\n<!-- /hermes-pack identity -->")


class StepCancelled(KeyboardInterrupt):
    """Return to the setup menu without treating cancellation as consent."""


class StepDeferred(Exception):
    """An optional setup action was left for later."""


def prompt_interface():
    return SimpleNamespace(ask=ask, yes=yes, choose=choose, StepDeferred=StepDeferred)


def terminal_ui(args):
    if args.plain or not sys.stdout.isatty() or os.environ.get("TERM") == "dumb":
        return None
    try:
        from installer_ui import TerminalUI
    except ImportError:
        print("Using plain prompts. Launch ./hermes-pack for the Textual interface managed by uv.")
        return None
    return TerminalUI(StepCancelled)


def profile_name(value):
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", value) or value in {
            "default", "hermes", "test", "tmp", "root", "sudo"}:
        raise ValueError("Choose a non-reserved profile name using lowercase letters, numbers, - or _.")
    return value


def identity_prompt(existing, name):
    if not isinstance(name, str) or not name.strip() or not 1 <= len(name) <= 80 or any(not (c.isalnum() or c in " -_.'") for c in name):
        raise ValueError("Use an assistant name of 1–80 letters, numbers, spaces, hyphens, underscores, dots or apostrophes.")
    if existing is None:
        existing = ""
    if not isinstance(existing, str):
        raise ValueError("agent.system_prompt must be text; review it in native configuration.")
    matches = list(IDENTITY.finditer(existing))
    if (len(matches) > 1 or existing.count("<!-- hermes-pack identity -->") != len(matches)
            or existing.count("<!-- /hermes-pack identity -->") != len(matches)):
        raise ValueError("Review the duplicate or incomplete identity block in agent.system_prompt.")
    block = "<!-- hermes-pack identity -->\nYour name is " + json.dumps(name, ensure_ascii=False) + ".\n<!-- /hermes-pack identity -->"
    return IDENTITY.sub(lambda _: block, existing) if matches else (existing.rstrip() + "\n\n" + block).lstrip()


def load_install_config(path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"Invalid JSON number: {value}")

    plan = json.loads(path.read_text(), object_pairs_hook=unique_keys, parse_constant=invalid_constant)
    json.dumps(plan, allow_nan=False)
    allowed = {"profile", "agent_name", "settings", "setup", "gateway", "delivery"}
    if not isinstance(plan, dict) or set(plan) - allowed:
        raise ValueError("Installation JSON must be an object with only: " + ", ".join(sorted(allowed)))
    if "profile" in plan:
        if not isinstance(plan["profile"], str):
            raise ValueError("profile must be a string")
        profile_name(plan["profile"])
    if "agent_name" in plan:
        if not isinstance(plan["agent_name"], str):
            raise ValueError("agent_name must be a string")
        identity_prompt(None, plan["agent_name"])
    if plan.get("gateway", "preserve") not in ("preserve", "start", "stop"):
        raise ValueError("gateway must be preserve, start, or stop")
    if "delivery" in plan:
        integrations.validate_destination(plan["delivery"])
    setup = plan.get("setup", {})
    actions = {"builtin_skills", "web_search", "browser", "paseo", "seed_user", "seed_cron", "pack_location"}
    if not isinstance(setup, dict) or set(setup) - actions or any(type(v) is not bool for v in setup.values()):
        raise ValueError("setup accepts only booleans for: " + ", ".join(sorted(actions)))
    settings = plan.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("settings must map native Hermes configuration keys to values")

    def check_setting(key, value):
        if re.search(r"(?:^|[._])(token|password|secret|api_key|credentials?|authorization)(?:$|[._])", key, re.I):
            raise ValueError(f"Keep credential field {key} in native account setup, not installation JSON")
        if isinstance(value, dict):
            for child, item in value.items():
                check_setting(key + "." + child, item)
        elif isinstance(value, list):
            for item in value:
                check_setting(key, item)

    for key, value in settings.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*", key):
            raise ValueError(f"Invalid native configuration key: {key}")
        if key in ("agent", "agent.system_prompt"):
            raise ValueError("Use agent_name for identity and dotted agent settings for other choices")
        if any(other.startswith(key + ".") for other in settings):
            raise ValueError(f"Overlapping native configuration keys below {key}; use separate leaf keys")
        check_setting(key, value)
    return plan


def ask(question, default=""):
    if UI:
        return UI.field(question, default)
    answer = input(f"{question}{' [' + default + ']' if default else ''}: ").strip()
    return answer or default


def yes(question, default=False, *, yes_help="Do this now", no_help="Leave this unchanged"):
    if UI:
        return UI.confirm(question, default, yes_help=yes_help, no_help=no_help)
    print(f"Yes: {yes_help}\nNo: {no_help}")
    while True:
        answer = ask(question + " (y/n)", "y" if default else "n").lower()
        if answer in ("y", "yes", "n", "no"):
            return answer in ("y", "yes")


def choose(question, options, default):
    if UI:
        return UI.select(question, options, default)
    for index, (_, label) in enumerate(options, 1):
        print(f"{index}. {label}")
    selected = ask(question, str(next(i for i, (v, _) in enumerate(options, 1) if v == default)))
    if selected.isdigit() and 1 <= int(selected) <= len(options):
        return options[int(selected) - 1][0]
    raise ValueError("Choose one of the listed options.")


def pick_profile(root, allow_new=True):
    root = root.expanduser().resolve()
    directory = root / "profiles"
    if directory.is_symlink():
        raise ValueError("Refusing a linked profiles directory.")
    names = []
    for path in sorted(directory.iterdir()) if directory.exists() else []:
        try:
            profile_name(path.name)
        except ValueError:
            continue
        if (not path.is_symlink() and (path / "config.yaml").is_file()
                and not (directory / ".deleted" / path.name).exists()):
            names.append(path.name)
    if not allow_new:
        if not names:
            raise StepDeferred("No installed named profiles found in this Hermes root.")
        return choose("Choose an installed profile", [(name, name) for name in names], names[0])
    if not UI:
        print("Existing profiles: " + (", ".join(names) or "none"))
    while True:
        try:
            name = profile_name(UI.profile(names) if UI else ask("Existing or new profile name"))
            if name not in names and os.path.lexists(directory / name):
                raise ValueError("That name belongs to an unavailable profile; use another name or repair it first.")
            return name  # Creation remains in step 1, after its normal setup choices.
        except ValueError as error:
            print(error)


def find_chromium(env):
    """Find Chrome on PATH, in the download cache, or in macOS applications."""
    candidates = [shutil.which(name, path=env.get("PATH", "")) for name in
                   ("google-chrome", "chromium", "chromium-browser", "chrome")]
    cache = Path.home() / ".agent-browser/browsers"
    versions = sorted(cache.glob("chrome-*"),
                      key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.name)), reverse=True)
    candidates += [p / "chrome" for p in versions]
    if sys.platform == "darwin":
        for base in (Path.home() / "Applications", Path("/Applications")):
            candidates += [base / f"{name}.app/Contents/MacOS/{name}" for name in
                           ("Google Chrome", "Chromium", "Google Chrome for Testing")]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    return None


def command(argv, env, capture=False, unattended=False):
    # Secrets are entered in native login prompts, never command arguments.
    if not capture:
        print("\n$ " + shlex.join([str(arg) for arg in argv]), flush=True)
    result = subprocess.run([str(arg) for arg in argv], env=env, text=True,
                            capture_output=capture, check=False,
                            stdin=subprocess.DEVNULL if unattended else None)
    if result.returncode:
        raise RuntimeError(f"Command failed (exit {result.returncode}): {argv[0]}")
    return result.stdout if capture else None


class Installer:
    def __init__(self, root, profile, hermes, agent_name=None):
        profile_name(profile)
        self.root = root.expanduser().resolve()
        if self.root.parent.name == "profiles":
            raise ValueError("--root must name the Hermes installation root, not a profile.")
        if self.root == REPO or REPO in self.root.parents:
            raise ValueError("Keep the Hermes installation and credentials outside this repository.")
        if (self.root / "profiles").is_symlink():
            raise ValueError("Refusing a linked profiles directory.")
        self.profile = profile
        self.home = self.root / "profiles" / profile
        self.hermes = hermes
        self.agent_name = agent_name
        self.env = {**os.environ, "HERMES_HOME": str(self.root)}
        # Do not inherit another profile's Python module search path.
        self.env.pop("PYTHONPATH", None)
        self.env.pop("PYTHONHOME", None)
        self.env["PATH"] = str(Path.home() / ".local/bin") + os.pathsep + self.env.get("PATH", "")
        self.failure_destination = None
        self.unattended = False

    def cli(self, *args, capture=False, global_command=False):
        prefix = [] if global_command else ["-p", self.profile]
        if self.unattended and args[:2] in (("profile", "install"), ("profile", "update"), ("profile", "delete")):
            args = (*args, "--yes")
        return command([self.hermes, *prefix, *args], self.env, capture, unattended=self.unattended)

    def config(self, key):
        try:
            return json.loads(self.cli("config", "get", key, "--json", capture=True))
        except RuntimeError:
            return None

    def require_profile(self):
        if self.home.is_symlink():
            raise ValueError("Refusing a linked profile directory.")
        if not (self.home / "config.yaml").is_file():
            raise ValueError("Install or import the profile first (step 1).")

    def require_stopped(self):
        if self.gateway_running():
            raise ValueError("Stop this profile's gateway before changing its configuration.")

    def gateway_running(self):
        if self.home.is_symlink():
            raise ValueError("Refusing a linked profile directory.")
        for name in ("gateway.pid", "gateway_state.json"):
            path = self.home / name
            if not path.exists():
                continue
            state = json.loads(path.read_text())
            pid = state.get("pid") if isinstance(state, dict) else state
            if not isinstance(pid, int) or pid <= 0:
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            return True
        return False

    def setup_status(self):
        if not (self.home / "config.yaml").is_file():
            return {i: "Profile not created" for i in range(1, len(STEPS) + 1)}
        preferences = self.home / "memories/USER.md"
        jobs = integrations.jobs(self)
        enabled = sum(bool(job.get("enabled", True)) for job in jobs)
        return {
            1: "Pack files installed" if (self.home / "distribution.yaml").is_file() else "Pack not installed",
            2: "Preferences saved" if preferences.is_file() and preferences.read_text().strip() else "No saved preferences",
            3: "AI selected; sign-in not checked" if self.config("model.default") else "AI not selected",
            4: integrations.status(self), 5: "Tool permissions not checked",
            6: "Reference installed; delegation not checked" if (self.home / "skills/paseo/SKILL.md").is_file() else "Reference not installed",
            7: "Skill installed; sign-in not checked" if (self.home / "skills/proton-cli/SKILL.md").is_file() else "Skill not installed",
            8: "Extra connections not checked",
            9: f"{enabled} enabled, {len(jobs) - enabled} paused" if jobs else "No scheduled tasks",
            10: "Service running; replies not tested" if self.gateway_running() else "Service stopped — assistant offline",
        }

    def backup(self):
        directory = self.root / "backups" / "pack-install"
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = directory / f"{self.profile}-{datetime.now():%Y%m%d-%H%M%S-%f}.zip"
        self.cli("backup", "--output", str(path), "--keep", "0")
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError("Backup was not created; stopping before making changes.")
        path.chmod(0o600)
        pattern = re.compile(re.escape(self.profile) + r"-\d{8}-\d{6}-\d{6}\.zip")
        for older in directory.iterdir():
            if pattern.fullmatch(older.name) and older.name < path.name and older.is_file() and not older.is_symlink():
                older.unlink()
        print(f"Private backup (contains credentials): {path}")
        return path

    def profile_setup(self):
        if self.home.is_symlink():
            raise ValueError("Refusing a linked profile directory.")
        if self.home.exists():
            if (self.root / "profiles/.deleted" / self.profile).exists():
                raise ValueError("This deleted profile has leftover files; finish its uninstall before installing again.")
            if (self.home / "distribution.yaml").is_file():
                self.require_profile()
                print(f"Continuing setup for {self.profile}; existing configuration and data are preserved.")
                return
            if not self.unattended and not yes("Install the pack into this existing profile?",
                   yes_help="Make a backup, then replace its setup and pack files",
                   no_help="Keep this profile unchanged; do not install the pack"):
                raise StepDeferred("Pack installation deferred; existing profile kept.")
            self.backup()
        else:
            # Native create clears deletion state; distribution install alone does not.
            self.cli("profile", "create", self.profile, "--no-alias", "--no-skills", global_command=True)
        self.cli("profile", "install", str(REPO), "--name", self.profile, "--force", global_command=True)
        self.require_profile()
        if not (self.home / "distribution.yaml").is_file():
            raise RuntimeError("Pack installation did not finish; retry the profile section.")

    def update_profile(self):
        self.require_profile()
        self.require_stopped()
        if not (self.home / "distribution.yaml").is_file():
            raise ValueError("This profile has no installed distribution to update.")
        self.cli("profile", "info", self.profile, global_command=True)
        self.backup()
        self.cli("profile", "update", self.profile, global_command=True)
        self.sync_identity()
        print("Update command finished. Local configuration, personal data, and independent extensions are preserved.")

    def uninstall_profile(self):
        self.require_stopped()
        installed = (self.home / "config.yaml").is_file()
        if installed:
            self.require_profile()
        elif self.home.exists() and not (self.root / "profiles/.deleted" / self.profile).exists():
            raise ValueError("This profile directory is incomplete but not marked deleted; inspect it before removal.")
        if not self.home.exists():
            print(f"Profile {self.profile} is already uninstalled.")
            return
        print(f"Remove profile: {self.profile}\nLocation: {self.home}")
        print("Removal includes this profile's configuration, credentials, memories, conversations, "
              "skills, plugins, schedules, and gateway service. A private backup is retained outside the profile. "
              "Hermes itself and shared applications remain installed.")
        question = "Back up and remove this profile?" if installed else "Archive remaining files from this deleted profile?"
        if not self.unattended and not yes(question, yes_help="Remove this profile and its gateway; preserve files in a private backup or archive",
                   no_help="Cancel removal and keep everything"):
            raise StepDeferred("Uninstall cancelled; profile kept.")
        if installed:
            self.backup()
            self.cli("profile", "delete", self.profile, global_command=True)
            if self.home.exists() and ((self.home / "config.yaml").exists()
                                       or not (self.root / "profiles/.deleted" / self.profile).exists()):
                raise StepDeferred("Removal was not completed; profile and backup kept.")
        if self.home.exists():
            if (self.home / "config.yaml").exists() or not (self.root / "profiles/.deleted" / self.profile).exists():
                raise ValueError("Profile state changed during cleanup; remaining files were left in place.")
            archive = self.root / "backups/pack-uninstall" / f"{self.profile}-{datetime.now():%Y%m%d-%H%M%S-%f}"
            archive.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.home.rename(archive)
            print(f"Archived deleted-profile leftovers: {archive}")
        print(f"Profile {self.profile} and its gateway service are removed. Existing backups are retained.")

    def identity_setup(self):
        existing = self.config("agent.system_prompt")
        match = IDENTITY.search(existing) if isinstance(existing, str) else None
        current = json.loads(match.group(1)) if match else self.profile
        if not isinstance(current, str):
            raise ValueError("Review the assistant name in agent.system_prompt; it must be text.")
        name = self.agent_name or (current if self.unattended else ask("Assistant name (how it introduces itself)", current))
        updated = identity_prompt(existing, name)
        self.agent_name = name
        if updated != existing:
            self.cli("config", "set", "agent.system_prompt", updated, capture=True)
            print(f"Assistant name saved: {name}")
        self.sync_identity(name)

    def sync_identity(self, name=None):
        if name is None:
            existing = self.config("agent.system_prompt")
            match = IDENTITY.search(existing) if isinstance(existing, str) else None
            if not match:
                return
            name = json.loads(match.group(1))
        soul = self.home / "SOUL.md"
        if soul.is_symlink():
            raise ValueError("Refusing a linked SOUL.md.")
        soul.write_text(identity_prompt(soul.read_text(), name) + "\n")

    def data_setup(self):
        print("Existing memories, diary, conversations, and learned skills are preserved.")
        if yes("Choose which skills this assistant can use?",
               yes_help="Open the skill selector; skills are instructions for specific tasks",
               no_help="Keep the current skill selection"):
            self.cli("skills", "config")
        if yes("Review the starting preferences?",
               yes_help="Show the suggested facts and preferences before saving anything",
               no_help="Keep your current preferences"):
            template = self.home / "templates/USER.md"
            content = template.read_text().strip()
            if not content:
                content = ask("Starting facts or preferences (leave empty to skip)", "").strip()
                if content:
                    template = self.home / "local/starting-user.md"
                    if template.parent.is_symlink() or template.is_symlink():
                        raise ValueError("Refusing linked starting preferences.")
                    template.parent.mkdir(mode=0o700, exist_ok=True)
                    template.write_text(content + "\n")
                    template.chmod(0o600)
            if content:
                print(content)
            if content and yes("Save these starting preferences if none are saved yet?",
                   yes_help="Fill an empty preferences file; existing preferences stay",
                   no_help="Do not save these suggestions"):
                command([sys.executable, self.home / "scripts/seed-user.py", self.home, "--template", template], self.env)
        if yes("Let the assistant find this pack to suggest future improvements?", True,
               yes_help="Save this folder location; publishing changes still needs approval",
               no_help="Do not save the pack location"):
            self.register_checkout()

    def register_checkout(self):
        directory = self.home / "local"
        target = directory / "pack-repo"
        if directory.is_symlink() or target.is_symlink():
            raise ValueError("Refusing a linked pack checkout setting.")
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        target.write_text(str(REPO) + "\n")

    def apply_install_config(self, plan):
        for key, value in plan.get("settings", {}).items():
            encoded = value if isinstance(value, str) else json.dumps(value, allow_nan=False)
            self.cli("config", "set", key, encoded)
        actions = plan.get("setup", {})
        if actions.get("pack_location"):
            self.register_checkout()
        if actions.get("builtin_skills"):
            self.cli("skills", "opt-in", "--sync")
        if actions.get("web_search"):
            self.cli("tools", "post-setup", "ddgs")
        if actions.get("browser"):
            self.reuse_browser_cli()
            self.cli("tools", "post-setup", "agent_browser")
            self.reuse_browser_cli()
            self.browser_setup()
        if actions.get("paseo"):
            integrations.paseo.install_reference(self)
        for key, script in (("seed_user", "seed-user.py"), ("seed_cron", "seed-cron.py")):
            if actions.get(key):
                extra = ["--hermes", self.hermes, "--deliver",
                         integrations.seed_destination(self, override=plan.get("delivery"))] if key == "seed_cron" else []
                command([sys.executable, self.home / "scripts" / script, self.home, *extra],
                        self.env, unattended=True)

    def account_setup(self):
        self.cli("setup", "model")

    def messaging_setup(self):
        integrations.setup(self, prompt_interface())

    def skills_setup(self):
        if yes("Add Hermes's built-in task skills?", True,
               yes_help="Install its bundled instructions for common tasks",
               no_help="Keep the currently installed skills"):
            self.cli("skills", "opt-in", "--sync")
        if yes("Choose which tools the assistant may use in chat?",
               yes_help="Open tool permissions for your connected apps",
               no_help="Keep current tool permissions"):
            self.cli("tools")
        for provider, title in (("ddgs", "web search with DuckDuckGo"),
                                ("agent_browser", "web browsing with Chrome")):
            if yes(f"Set up {title}?",
                   yes_help="Download the required software; browser setup may request administrator access",
                   no_help="Do not install anything; this feature may remain unavailable"):
                if provider == "agent_browser":
                    self.reuse_browser_cli()
                self.cli("tools", "post-setup", provider)
                if provider == "agent_browser":
                    self.reuse_browser_cli()
        self.browser_setup()

    def browser_setup(self):
        configured = self.config("AGENT_BROWSER_EXECUTABLE_PATH")
        current = shutil.which(os.path.expanduser(configured), path=self.env["PATH"]) if isinstance(configured, str) else None
        if current:
            print(f"Browser already configured: {current}")
            return
        if self.unattended and configured:
            raise ValueError("Configured AGENT_BROWSER_EXECUTABLE_PATH is unavailable; correct it in settings")
        detected = find_chromium(self.env)
        if self.unattended:
            if not detected:
                raise ValueError("No Chrome/Chromium executable found; set AGENT_BROWSER_EXECUTABLE_PATH in settings")
            self.cli("config", "set", "AGENT_BROWSER_EXECUTABLE_PATH", str(detected))
            return
        options = [(str(detected), f"Use detected browser: {detected}")] if detected else []
        options += [("manual", "Enter another browser path"), ("skip", "Keep current setting")]
        selected = choose("Chrome / Chromium executable", options, str(detected) if detected else "skip")
        if selected == "skip":
            return
        value = ask("Chromium/Chrome executable path (blank to cancel)") if selected == "manual" else selected
        if not value:
            return
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("Choose an executable browser file.")
        self.cli("config", "set", "AGENT_BROWSER_EXECUTABLE_PATH", str(path))

    def reuse_browser_cli(self):
        target = self.home / "bin/browser-use"
        if os.path.lexists(target):
            return  # Preserve existing launchers, including links needing owner repair.
        search = str(self.root / "bin") + os.pathsep + self.env["PATH"]
        source = shutil.which("browser-use", path=search)
        if not source:
            uv = shutil.which("uv", path=str(self.home / "bin") + os.pathsep + search)
            if not uv:
                return
            directory = command([uv, "tool", "dir"], self.env, capture=True).strip()
            source = shutil.which("browser-use", path=str(Path(directory) / "browser-use/bin"))
        if source:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(Path(source).resolve())
            print("Reused installed Browser Use CLI for this profile.")

    def paseo_setup(self):
        if yes("Add instructions for delegating work through Paseo?",
               yes_help="Install Paseo's upstream reference in this profile; no worker is launched",
               no_help="Keep the current skills"):
            integrations.paseo.install_reference(self)

    def proton_setup(self):
        executable = shutil.which("proton", path=self.env["PATH"])
        if not executable:
            if not yes("Install the community Proton command-line app?",
                       yes_help="Download version 3.8.0 using its installer; this is not an official Proton app",
                       no_help="Leave Proton integration for later"):
                raise ValueError("Proton deferred.")
            with tempfile.TemporaryDirectory(prefix="proton-install-") as temporary:
                installer = Path(temporary) / "install.sh"
                with urllib.request.urlopen("https://raw.githubusercontent.com/roman-16/proton-cli/v3.8.0/scripts/install.sh",
                                            timeout=30) as response:
                    installer.write_bytes(response.read())
                command(["sh", installer, "--version", "3.8.0"], self.env)
            executable = shutil.which("proton", path=self.env["PATH"])
            if not executable:
                raise ValueError("Proton was not found after installation.")
        command([executable, "--version"], self.env)
        directory = (Path.home() / "Library/Application Support/proton-cli" if sys.platform == "darwin"
                     else Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "proton-cli")
        policy = directory / "config.yaml"
        print(f"Proton policy: {policy}. This is shared by this OS account. "
              "The pack policy permits Mail/Calendar reads and blocks mutations.")
        if yes("Set Proton access to read-only?",
               yes_help="Back up the existing policy, then allow email/calendar reads and block changes for this computer account",
               no_help="Keep the current Proton access policy"):
            if policy.is_symlink() or directory.is_symlink():
                raise ValueError("Refusing a linked Proton policy directory/file.")
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            if policy.exists():
                backup = directory / f"config-{datetime.now():%Y%m%d-%H%M%S-%f}.yaml.bak"
                shutil.copy2(policy, backup)
                backup.chmod(0o600)
            shutil.copyfile(self.home / "proton-cli.yaml", policy)
            policy.chmod(0o600)
        if yes("Install instructions for using Proton?", True,
               yes_help="Generate and scan the app's instructions, then install them if the scan allows it",
               no_help="Keep the current Proton skill"):
            python = Path(ask("Hermes Python executable", str(self.root / "hermes-agent/venv/bin/python")))
            script = """
import pathlib, subprocess, tempfile, shutil, sys
from tools.skills_guard import scan_skill, should_allow_install, format_scan_report
with tempfile.TemporaryDirectory(prefix='proton-skill-') as temporary:
    source = pathlib.Path(temporary) / 'proton-cli'
    source.mkdir()
    (source / 'SKILL.md').write_text(subprocess.check_output([sys.argv[1], 'skill'], text=True))
    result = scan_skill(source, source='https://github.com/roman-16/proton-cli')
    print(format_scan_report(result))
    allowed, reason = should_allow_install(result)
    if not allowed:
        raise SystemExit(reason)
    target = pathlib.Path(sys.argv[2]) / 'skills/proton-cli'
    if target.is_symlink() or target.parent.is_symlink() or (target / 'SKILL.md').is_symlink():
        raise SystemExit('Refusing a linked skill destination')
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / 'SKILL.md', target / 'SKILL.md')
"""
            command([python, "-c", script, executable, self.home],
                    {**self.env, "HERMES_HOME": str(self.home), "PYTHONPATH": str(self.root / "hermes-agent")})
        if yes("Sign in to Proton now?",
               yes_help="Open the Proton app's password and verification prompts",
               no_help="Keep any existing sign-in; connect later if needed"):
            command([executable, "account", "login"], self.env)

    def extensions_setup(self):
        if yes("Connect more apps and services?",
               yes_help="Open Hermes's connection catalog; some apps require a separate account",
               no_help="Keep current app connections"):
            self.cli("mcp")
        if yes("Browse optional add-ons?",
               yes_help="Show available Hermes plugins; you choose whether to install one",
               no_help="Keep current add-ons"):
            self.cli("plugins", "browse")
            identifier = ask("Plugin identifier to install (blank to skip)")
            if identifier:
                self.cli("plugins", "install", identifier)
        if yes("Connect an external memory service?",
               yes_help="Open setup for a separate memory provider",
               no_help="Keep Hermes's built-in memory and any existing provider"):
            self.cli("memory", "setup")

    def scheduling_setup(self):
        self.cli("cron", "list")
        if yes("Add an hourly personal check-in?",
               yes_help="Create the starter review once; existing schedules stay as they are",
               no_help="Do not add the starter review; existing tasks keep their schedules"):
            command([sys.executable, self.home / "scripts/seed-cron.py", self.home,
                     "--hermes", self.hermes, "--deliver",
                     integrations.seed_destination(self, prompt_interface())], self.env)
        integrations.configure_failures(self, prompt_interface())

    def gateway_setup(self):
        self.cli("doctor")
        self.cli("cron", "list")
        print("Run this profile’s background service on only one host. Review restored jobs before starting.")
        if yes("Run the assistant in the background on this computer?",
               yes_help="Set up and start the service for chat and scheduled work",
               no_help="Keep the service as it is; a stopped service stays offline"):
            self.cli("gateway", "install")
            self.cli("gateway", "start")
            self.cli("gateway", "status")


STEPS = (
    ("Profile & identity", "profile_setup"),
    ("Personal data & preferences", "data_setup"),
    ("AI model & sign-in", "account_setup"),
    ("Messaging", "messaging_setup"),
    ("Tools & built-in skills", "skills_setup"),
    ("Paseo delegation", "paseo_setup"),
    ("Proton", "proton_setup"),
    ("Extra apps & add-ons", "extensions_setup"),
    ("Scheduled tasks & alerts", "scheduling_setup"),
    ("Background service", "gateway_setup"),
)


def main():
    global UI
    parser = argparse.ArgumentParser(prog="hermes-pack", description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", help="Profile name (opens a picker if omitted)")
    common.add_argument("--root", type=Path, default=Path.home() / ".hermes", help="Hermes installation root")
    common.add_argument("--hermes", default="hermes", help="Hermes CLI executable")
    common.add_argument("--plain", action="store_true", help="Use line prompts instead of the TUI")
    common.add_argument("-y", "--yes", action="store_true",
                        help="Run without prompts; requires a profile. Optional setup is applied only with --config")
    common.add_argument("--config", type=Path, help="JSON choices for unattended install/update")
    common.add_argument("--check", action="store_true", help="Validate --config structure and choices without changing anything")
    commands = parser.add_subparsers(dest="action", metavar="{install,update,uninstall}")
    create = commands.add_parser("install", parents=[common], help="Create or finish configuring a profile")
    create.add_argument("--agent-name", help="Conversational name (prompted if omitted)")
    commands.add_parser("update", parents=[common], help="Update the installed distribution, preserving local data and config")
    commands.add_parser("uninstall", parents=[common], help="Back up and remove a selected profile")
    args = parser.parse_args()
    if not args.action:
        parser.print_help()
        return
    if (args.config or args.check) and args.action == "uninstall":
        parser.error("--config and --check are for install/update only")
    if args.check and not args.config:
        parser.error("--check requires --config FILE")
    plan = load_install_config(args.config.expanduser()) if args.config else {}
    if args.profile and plan.get("profile") and args.profile != plan["profile"]:
        parser.error("--profile conflicts with profile in the JSON file")
    args.profile = args.profile or plan.get("profile")
    agent_name = getattr(args, "agent_name", None)
    if agent_name and plan.get("agent_name") and agent_name != plan["agent_name"]:
        parser.error("--agent-name conflicts with agent_name in the JSON file")
    agent_name = agent_name or plan.get("agent_name")
    if agent_name:
        identity_prompt(None, agent_name)
    if args.config:
        if not args.profile:
            parser.error("Specify profile in the JSON file or pass --profile NAME")
        profile_name(args.profile)
        if args.check:
            print("Installation JSON is valid. No changes made; credentials, native setting names, and external services are not checked.")
            return
        if not args.yes:
            parser.error("Applying --config requires --yes; use --check to validate without changes")
    if args.yes and not args.profile:
        parser.error("--yes requires an explicit --profile")
    os.umask(0o077)
    hermes = shutil.which(args.hermes, path=str(Path.home() / ".local/bin") + os.pathsep + os.environ.get("PATH", ""))
    if not hermes:
        parser.exit(1, "Install Hermes first: https://hermes-agent.nousresearch.com/docs/getting-started/installation\n")
    if not args.yes and not sys.stdin.isatty():
        parser.exit(1, "Use an interactive terminal, or pass --yes --profile NAME for unattended operation.\n")
    UI = None if args.yes else terminal_ui(args)
    profile = args.profile or pick_profile(args.root, allow_new=args.action == "install")
    setup = Installer(args.root, profile, hermes, agent_name)
    setup.unattended = args.yes
    if args.action != "install" or args.yes:
        if UI:
            UI.section = args.action.capitalize()
        if args.action == "update":
            setup.require_profile()
        running = setup.gateway_running()
        if running:
            if (args.yes and os.environ.get("HERMES_SESSION_ID")
                    and Path(os.environ.get("HERMES_HOME", "")).resolve() == setup.home):
                raise ValueError("Run this operation outside the active gateway using systemd-run --user; see README Agent use.")
            if not args.yes and not yes(f"Pause the assistant to {args.action} this profile?", True,
                       yes_help="Stop its background service and proceed",
                       no_help="Leave it running and cancel this operation"):
                raise StepDeferred(f"{args.action.capitalize()} cancelled; gateway left running.")
            setup.cli("gateway", "stop")
            setup.require_stopped()
        if args.action == "install":
            if args.config and (setup.home / "distribution.yaml").is_file():
                setup.backup()
            setup.profile_setup()
            setup.identity_setup()
            if not args.config:
                print("Pack installed. Account sign-in, optional integrations, memory seeds, schedules, and initial service startup are separate setup steps.")
        if args.action == "update":
            setup.update_profile()
            if agent_name:
                setup.identity_setup()
        if args.config:
            setup.apply_install_config(plan)
            print("Applied the installation JSON choices.")
        if args.action == "uninstall":
            setup.uninstall_profile()
        elif plan.get("gateway") == "start":
            setup.cli("gateway", "install")
            setup.cli("gateway", "start")
            setup.cli("gateway", "status")
        elif plan.get("gateway") != "stop" and running and (args.yes or yes("Bring the assistant back online?", True,
                               yes_help="Start its background service for chat and scheduled work",
                               no_help="Leave the assistant stopped")):
            setup.cli("gateway", "start")
        return
    print(f"Pack checkout: {REPO}\nProfile: {setup.home}\nSecrets stay in native local stores.")
    print("Choose what to set up. Existing personal data is preserved.")
    detail = ""
    while True:
        statuses = setup.setup_status()
        if UI:
            selection = UI.menu(setup, STEPS, statuses, detail)
        else:
            print("\n0. Guided setup")
            for index, (title, _) in enumerate(STEPS, 1):
                print(f"{index}. {title}  [{statuses.get(index, 'Optional')}]")
            selection = ask("Step number, or q to finish", "0")
        if selection.lower() == "q":
            break
        if not selection.isdigit() or not 0 <= int(selection) <= len(STEPS):
            print("Choose one of the listed steps.")
            continue
        choice = int(selection)
        if choice == 0:
            if UI:
                selected = UI.sections(STEPS) or []
            else:
                values = ask("Sections to configure (comma-separated numbers)", "1,3,4,5")
                if any(not v.strip().isdigit() or not 1 <= int(v) <= len(STEPS) for v in values.split(",")):
                    print("Use the section numbers shown above.")
                    continue
                selected = [int(v) - 1 for v in values.split(",")]
        else:
            selected = [choice - 1]
        selected = sorted(set(selected))
        for index in selected:
            title, method = STEPS[index]
            if UI:
                UI.section = title
            print(f"\nStep {index + 1}: {title}")
            back = False
            while True:
                try:
                    if method != "gateway_setup" and setup.gateway_running():
                        if not yes("Pause the assistant while changing its settings?", True,
                                   yes_help="Stop chat and scheduled work; use the Background service step to start again",
                                   no_help="Leave it running and skip this setup section"):
                            raise StepDeferred("Gateway left running; configuration deferred.")
                        setup.cli("gateway", "stop")
                        setup.require_stopped()
                    if index and not (setup.home / "config.yaml").is_file():
                        if not yes("Create this assistant profile first?", True,
                                   yes_help="Create its settings folder and install the pack",
                                   no_help="Skip this section without creating a profile"):
                            raise StepDeferred("Create the profile when you are ready to configure it.")
                        setup.profile_setup()
                        setup.identity_setup()
                    if index:
                        if not (setup.home / "distribution.yaml").is_file():
                            setup.profile_setup()
                        setup.require_profile()
                    getattr(setup, method)()
                    if index == 0:
                        setup.require_profile()
                        setup.identity_setup()
                    detail = ""
                except StepCancelled:
                    detail = f"Left {title}. Earlier completed changes remain."
                    back = True
                except StepDeferred as error:
                    detail = str(error)
                    print(detail)
                except (OSError, ValueError, RuntimeError) as error:
                    detail = f"{title}: {error}"
                    if UI:
                        UI.notice(detail, error=True)
                    else:
                        print(detail)
                    try:
                        if choose("Next action", [("retry", "Retry this section"), ("later", "Leave for later")], "later") == "retry":
                            continue
                    except StepCancelled:
                        back = True
                break
            if back:
                break
    print("Setup closed. Completed changes remain; unfinished sections can be reopened next time.")


if __name__ == "__main__":
    try:
        main()
    except StepDeferred as notice:
        print(notice)
        sys.exit(1)
    except (EOFError, KeyboardInterrupt):
        print("\nSetup interrupted. Rerun and choose the unfinished step; completed changes remain.")
        sys.exit(130)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Setup stopped: {error}", file=sys.stderr)
        sys.exit(1)

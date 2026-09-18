"""Paseo setup and native scheduled-task routing."""

import json
import re

from .paseo import Paseo


def validate_destination(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*(?::[A-Za-z0-9._-]+)?", value):
        raise ValueError("Use a native delivery destination, such as local or paseo:CONVERSATION_UUID")
    return value


def status(installer):
    connection = Paseo(installer, None)
    return "Paseo: " + connection.status() if connection.enabled() else "No messaging connection enabled"


def setup(installer, prompts):
    Paseo(installer, prompts).setup()


def destination(installer, prompts=None):
    connection = Paseo(installer, prompts)
    return connection.destination() if connection.enabled() else "local"


def seed_destination(installer, prompts=None, override=None):
    if (installer.home / "local/seed-cron.done").exists() or any(
            str(job.get("name", "")).casefold() == "personal review" for job in jobs(installer)):
        return "local"
    return override or destination(installer, prompts)


def jobs(installer):
    path = installer.home / "cron/jobs.json"
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Refusing linked cron storage.")
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    result = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(result, list) or any(not isinstance(job, dict) for job in result):
        raise ValueError("Unexpected native cron storage; inspect it with Hermes.")
    return result


def route_failures(installer, prompts, target):
    if target is None:
        return
    validate_destination(target)
    for job in jobs(installer):
        label = "local logs without notifications" if target == "local" else target
        if prompts.yes(f"Use {label} for errors from '{job.get('name') or job['id']}'?",
                       job.get("name") == "Personal review",
                       yes_help="Save errors locally without notifications" if target == "local" else "Send this task's errors to the selected destination",
                       no_help="Keep this task's current error destination"):
            installer.cli("cron", "edit", job["id"], "--failure-deliver", target)


def configure_failures(installer, prompts):
    if not jobs(installer):
        return
    target = installer.failure_destination
    if target is None:
        selected = prompts.choose("Where should scheduled-task errors go?",
                                  [("keep", "Keep current routing"), ("local", "Local logs, without notifications")], "keep")
        if selected == "keep":
            return
        target = "local"
    route_failures(installer, prompts, target)

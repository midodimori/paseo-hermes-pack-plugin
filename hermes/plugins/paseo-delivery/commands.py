"""Native skill discovery/invocation and cron parsing for the Paseo provider."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
import re
import shlex
from uuid import UUID


def setup(parser):
    parser.add_argument("operation", choices=("skills", "skill", "cron"))
    parser.add_argument("--name")
    parser.add_argument("--arguments", default="")
    parser.add_argument("--session-id")


def execute(args):
    os.environ["HERMES_PLATFORM"] = "acp"
    if args.operation in {"skills", "skill"}:
        from agent.skill_commands import get_skill_commands, build_skill_invocation_message

        commands = get_skill_commands()
        if args.operation == "skills":
            result = {"commands": [
                {"name": key.lstrip("/"), "description": str(info["description"])[:200],
                 "argumentHint": "[instruction]"}
                for key, info in sorted(commands.items())
            ]}
        else:
            key = "/" + str(args.name or "").lstrip("/")
            if key not in commands:
                raise ValueError("This skill shortcut is no longer available in this profile")
            if not args.session_id:
                raise ValueError("Skill invocation requires a native Hermes session ID")
            session = str(UUID(args.session_id))
            message = build_skill_invocation_message(key, args.arguments, task_id=session)
            if message is None:
                raise ValueError("Hermes could not load the selected skill")
            result = {"message": message}
    else:
        from hermes_cli.cron import cron_command
        from hermes_cli.subcommands.cron import build_cron_parser

        output = StringIO()
        status = 0
        with redirect_stdout(output), redirect_stderr(output):
            parser = argparse.ArgumentParser(prog="hermes")
            build_cron_parser(parser.add_subparsers(), cmd_cron=cron_command)
            try:
                parsed = parser.parse_args(["cron", *shlex.split(args.arguments)])
                if parsed.cron_command == "tick":
                    raise ValueError("Scheduler ticks belong to the gateway; use /cron run <job-id> to request a run")
                if parsed.cron_command == "run":
                    from cron import trigger_job

                    job = trigger_job(parsed.job_id)
                    if job is None:
                        raise ValueError("Scheduled job not found")
                    print(f"Run requested: {job['name']} ({job['id']}). The gateway will execute it on its next tick.")
                else:
                    status = parsed.func(parsed) or 0
            except SystemExit as error:
                status = error.code
        text = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue()).strip()
        result = {"text": text, "failed": bool(status)}
    return result


def handle(args):
    try:
        result = execute(args)
    except (ValueError, OSError) as error:
        result = {"error": str(error)}
    print(json.dumps(result, ensure_ascii=False))

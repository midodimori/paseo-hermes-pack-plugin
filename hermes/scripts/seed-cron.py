"""Seed the native starter cron once during owner-run setup on macOS/Linux."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess


NAME = "Personal review"
PROMPT = (
    "Run a periodic personal review using review-context. Start from my current priorities "
    "and commitments, then follow relevant leads within standing permissions for recurring "
    "review. Check recent profile-local cron health as part of the review. "
    "Consider approaching deadlines even without new activity. Spend at most three "
    "minutes reviewing; this is an effort instruction, not a hard runtime limit. Recommend "
    "at most one useful next action or defer. Do not change projects, write memory, start "
    "workers, or execute recommendations. Report only a new actionable observation or "
    "material change worth my attention; otherwise reply exactly [SILENT]."
)


def seed_cron(home: Path, hermes_command=("hermes",), *, deliver="local") -> bool:
    home = home.expanduser().resolve(strict=True)
    if home.parent.name != "profiles" or not (home / "config.yaml").is_file():
        raise ValueError("Choose an installed named Hermes profile under profiles/")
    if not (home / "skills/review-context/SKILL.md").is_file():
        raise ValueError("Install the review-context skill first")
    local = home / "local"
    if local.is_symlink():
        raise ValueError("Refusing a linked local directory")
    local.mkdir(mode=0o700, exist_ok=True)
    marker = local / "seed-cron.done"
    descriptor = os.open(local / "seed-cron.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    # ponytail: serializes setup invocations only; run initial seeding before other job authors.
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if marker.is_symlink():
            raise ValueError("Refusing a linked seed marker")
        if marker.exists():
            return False

        def matching_jobs():
            path = home / "cron/jobs.json"
            if path.parent.is_symlink() or path.is_symlink():
                raise ValueError("Refusing linked cron storage")
            if not path.exists():
                return []
            data = json.loads(path.read_text(encoding="utf-8"))
            jobs = data.get("jobs") if isinstance(data, dict) else None
            if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
                raise ValueError("Unexpected native cron storage; inspect it with Hermes")
            matches = [job for job in jobs if str(job.get("name", "")).casefold() == NAME.casefold()]
            if len(matches) > 1:
                raise ValueError("Multiple Personal review jobs exist; reconcile them in Hermes")
            return matches

        existing = matching_jobs()
        if not existing:
            subprocess.run(
                [*hermes_command, "cron", "create", "every 1h", PROMPT,
                 "--name", NAME, "--skill", "review-context", "--continuity", "--deliver", deliver,
                 "--failure-deliver", "local", "--reasoning-effort", "medium"],
                env={**os.environ, "HERMES_HOME": str(home)}, check=True,
            )
            if not matching_jobs():
                raise RuntimeError("Hermes did not create the starter job in the selected profile")
        marker.touch(mode=0o600, exist_ok=False)
        return not existing


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile_home", type=Path, help="Explicit native Hermes profile directory")
    parser.add_argument("--hermes", default="hermes", help="Hermes executable if not on PATH")
    parser.add_argument("--deliver", default="local", help="Native findings destination; defaults to local logs")
    args = parser.parse_args()
    try:
        created = seed_cron(args.profile_home, (args.hermes,), deliver=args.deliver)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Starter cron was not initialized: {error}\n")
    print("Created the hourly Personal review job." if created else "Existing scheduling choices preserved; nothing created.")

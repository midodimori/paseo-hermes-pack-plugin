"""Initialize an empty native user memory during owner-run setup on macOS/Linux."""

import argparse
import fcntl
import os
from pathlib import Path
import tempfile


def seed_user(home: Path, template: Path) -> bool:
    home = home.expanduser().resolve(strict=True)
    if not (home / "config.yaml").is_file():
        raise ValueError("Choose an installed Hermes profile with config.yaml")
    content = template.read_text(encoding="utf-8").strip()
    if not content:
        return False
    directory = home / "memories"
    if directory.is_symlink():
        raise ValueError("Refusing a linked memory directory")
    directory.mkdir(mode=0o700, exist_ok=True)
    target = directory / "USER.md"
    # Share Hermes's USER.md.lock so its memory tool cannot race this empty check.
    descriptor = os.open(target.with_suffix(".md.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_symlink():
            raise ValueError("Refusing a linked user memory file")
        try:
            if target.read_bytes():
                return False
        except FileNotFoundError:
            pass
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as temporary:
            staged = Path(temporary.name)
            try:
                temporary.write(content + "\n")
                temporary.flush()
                os.replace(staged, target)
            finally:
                staged.unlink(missing_ok=True)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile_home", type=Path, help="Explicit native Hermes profile directory")
    parser.add_argument("--template", type=Path, default=Path(__file__).resolve().parents[1] / "templates/USER.md")
    args = parser.parse_args()
    try:
        written = seed_user(args.profile_home, args.template)
    except (OSError, ValueError) as error:
        parser.exit(1, f"User memory was not initialized: {error}\n")
    print("Initialized native USER.md from the starting facts." if written else "No starting facts written; existing user memory is preserved.")

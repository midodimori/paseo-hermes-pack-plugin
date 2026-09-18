"""Exercise Textual keyboard/mouse input and terminal handoff without changing a profile."""

from pathlib import Path
import contextlib
import io
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str((Path(__file__).resolve().parents[1] / "hermes")))
from install import StepCancelled, STEPS
import install
from installer_ui import PromptApp, TerminalUI


def main():
    ui = TerminalUI(StepCancelled)
    native_run = PromptApp.run

    def enter(keys, action, size=(100, 30), click=None):
        async def pilot(driver):
            await driver.pause()
            assert driver.app.query_one("#panel").region.width == size[0], "Use the available terminal width"
            assert driver.app.query_one("#heading").region.y <= 2
            assert driver.app.query_one("#help").region.bottom <= size[1], "Keyboard help must fit small terminals"
            if driver.app.error:
                assert driver.app.query_one("#error").display
            if driver.app.options is not None and len(driver.app.options) == 2:
                assert driver.app.query_one("#panel").region.height < 20, "Short prompts must stay compact"
            if click:
                assert await driver.click(click)
            else:
                await driver.press(*keys)
        def run(app, **kwargs):
            assert kwargs == {"inline": True}, "All prompts must preserve command output with the same layout"
            return native_run(app, headless=True, size=size, auto_pilot=pilot)
        with patch.object(PromptApp, "run", run):
            return action()

    assert enter(["enter"], lambda: ui.confirm("Replace profile?")) is False
    assert enter(["down", "enter"], lambda: ui.confirm("Replace profile?")) is True
    with patch.object(install, "UI", ui):
        assert enter(["enter"], lambda: install.yes("Pause the assistant?",
                     yes_help="Stop chat while changing settings", no_help="Keep it running")) is False
    with patch.object(ui, "select", return_value=True) as select:
        assert ui.confirm("Pause?", yes_help="Stop chat", no_help="Keep it running") is True
        assert select.call_args.args[1] == [(False, "No — Keep it running"), (True, "Yes — Stop chat")]
    assert enter(list("Étoile") + ["enter"], lambda: ui.field("Assistant name", "Preview")) == "Étoile"
    try:
        enter(["escape"], lambda: ui.confirm("Replace profile?"))
    except StepCancelled:
        pass
    else:
        raise AssertionError("Escape must cancel, never confirm")
    setup = SimpleNamespace(profile="assistant", home=Path("/tmp/preview/profiles/assistant"))
    assert enter(["escape"], lambda: ui.menu(setup, STEPS, {})) == "q"
    assert enter(["enter"], lambda: ui.menu(setup, STEPS, {1: "Installed"})) == "0"
    assert enter(["down", "enter"], lambda: ui.menu(setup, STEPS, {})) == "1"
    assert enter(["tab", "enter"], lambda: ui.sections(STEPS)) == [0, 2, 3, 4]
    assert enter(["space", "tab", "enter"], lambda: ui.sections(STEPS)) == [2, 3, 4]
    assert enter(["escape"], lambda: ui.sections(STEPS)) == []
    assert enter([], lambda: ui.sections(STEPS), click="#back") == []
    try:
        enter(["ctrl+c"], lambda: ui.field("Name"))
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("Ctrl+C must exit even when an input has focus")
    with tempfile.TemporaryDirectory() as temporary, patch.object(install, "UI", ui):
        root = Path(temporary)
        for name in ("alpha", "beta"):
            home = root / "profiles" / name
            home.mkdir(parents=True)
            (home / "config.yaml").write_text("{}\n")
        assert enter(["down", "enter"], lambda: install.pick_profile(root)) == "alpha"
        assert enter(["b", "down", "enter"], lambda: install.pick_profile(root)) == "beta"
        assert enter(list("gamma") + ["enter"], lambda: install.pick_profile(root)) == "gamma"
        assert not (root / "profiles/gamma").exists()
    assert enter([], lambda: ui.sections(STEPS), size=(60, 18), click="#continue") == [0, 2, 3, 4]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ui.notice("Profile setup failed", error=True)
    assert output.getvalue().strip() == "Profile setup failed"
    assert enter(["enter"], lambda: ui.select("Next action", [("retry", "Retry"), ("later", "Leave for later")], "later")) == "later"
    assert ui.error == "", "The error belongs to the next prompt only"
    print("Textual navigation, safe defaults, editable profiles, cancellation, small-terminal mouse input, and terminal handoff passed.")


if __name__ == "__main__":
    main()

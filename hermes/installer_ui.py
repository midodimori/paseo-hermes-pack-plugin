"""Consistent Textual prompts; each returns the terminal to native Hermes commands."""

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, OptionList, SelectionList, Static


class PromptApp(App):
    TITLE = "HERMES PACK"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("ctrl+c", "interrupt", "Exit", priority=True),
        Binding("down", "suggestion(1)", show=False),
        Binding("up", "suggestion(-1)", show=False),
    ]
    CSS = """
    Screen { height: auto; max-height: 100%; background: #10151d; }
    #panel { width: 100%; height: auto; max-height: 100%;
             border: round #344154; padding: 0 2; background: #151c26; }
    #heading { height: 2; padding-top: 1; color: #78cbd4; text-style: bold; }
    #body { height: auto; max-height: 55vh; }
    #question { height: auto; margin: 1 0; color: #edf2f7; text-style: bold; }
    #detail { height: auto; color: #a3b0c2; margin-bottom: 1; }
    #error { height: auto; border-left: thick #e1a56b; padding-left: 1;
             margin: 1 0; color: #efbf93; }
    Input { margin-bottom: 1; background: #10151d; border: tall #344154; }
    Input:focus { border: tall #78cbd4; }
    OptionList, SelectionList { height: auto; max-height: 12; min-height: 1;
                               background: #151c26; border: none; padding: 0; }
    OptionList > .option-list--option-highlighted { background: #263f50; color: #eaf8fa; }
    #actions { height: 1; margin-top: 1; }
    Button { min-width: 12; margin-right: 2; background: #263343; color: #d6e0eb; }
    #continue { background: #285b68; color: #effbff; }
    Button:focus { text-style: bold reverse; }
    #help { height: auto; margin: 1 0 0 0; color: #8291a6; }
    """

    def __init__(self, section, question, *, detail="", options=None, default=None,
                 multiple=False, names=None, cancel_value=None, error=""):
        super().__init__()
        self.theme = "textual-dark"
        self.section, self.question, self.detail = section, question, detail
        self.options, self.default, self.multiple = options, default, multiple
        self.names, self.cancel_value = names, cancel_value
        self.error = error
        self.cancelled = self.interrupted = False

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield from self.content()

    def content(self) -> ComposeResult:
        yield Static(f"HERMES PACK  /  {self.section}", id="heading", markup=False)
        with VerticalScroll(id="body"):
            yield Static(self.question, id="question", markup=False)
            if self.error:
                yield Static(self.error, id="error", markup=False)
            if self.detail:
                yield Static(self.detail, id="detail", markup=False)
            if self.options is None:
                yield Input(value=self.default or "", id="field", placeholder="Type a profile name" if self.names is not None else "")
                if self.names:
                    yield OptionList(*self.names, id="suggestions", markup=False)
            elif self.multiple:
                yield SelectionList(*[(label, value, value in self.default) for value, label in self.options], id="choices")
            else:
                yield OptionList(*[label for _, label in self.options], id="choices", markup=False)
        with Horizontal(id="actions"):
            yield Button("Continue" if self.multiple else "Confirm choice", variant="primary", id="continue", compact=True)
            yield Button("Back", id="back", compact=True)
        if self.multiple:
            hint = "↑ ↓ Choose · Space Toggle · Tab Continue"
        elif self.options is None:
            hint = "Type to edit · ↑ ↓ Existing profiles · Enter Confirm" if self.names else "Type to edit · Enter Confirm"
        else:
            hint = "↑ ↓ Choose · Enter Confirm"
        yield Static(hint + " · Esc Back · Ctrl+C Exit", id="help", markup=False)

    def on_mount(self):
        if self.options is None:
            self.query_one(Input).focus()
            if self.names:
                self.query_one("#suggestions", OptionList).highlighted = None
        else:
            choices = self.query_one("#choices", OptionList)
            if not self.multiple:
                choices.highlighted = next(i for i, (value, _) in enumerate(self.options) if value == self.default)
            choices.focus()

    def action_back(self):
        self.cancelled = True
        self.exit(self.cancel_value)

    def action_interrupt(self):
        self.interrupted = True
        self.exit()

    def action_suggestion(self, direction):
        if self.names and isinstance(self.focused, Input):
            choices = self.query_one("#suggestions", OptionList)
            if choices.option_count:
                index = choices.highlighted
                choices.highlighted = (0 if direction > 0 else choices.option_count - 1) if index is None else (index + direction) % choices.option_count

    @on(Input.Changed)
    def filter_profiles(self, event):
        if self.names:
            choices = self.query_one("#suggestions", OptionList)
            choices.clear_options()
            choices.add_options(name for name in self.names if name.startswith(event.value))
            choices.highlighted = None

    @on(Input.Submitted)
    @on(Button.Pressed, "#continue")
    def accept(self):
        if self.options is None:
            if self.names:
                suggestions = self.query_one("#suggestions", OptionList)
                if suggestions.highlighted is not None:
                    self.exit(str(suggestions.get_option_at_index(suggestions.highlighted).prompt))
                    return
            self.exit(self.query_one(Input).value.strip() or self.default or "")
        elif self.multiple:
            self.exit(self.query_one(SelectionList).selected)
        else:
            index = self.query_one("#choices", OptionList).highlighted
            if index is not None:
                self.exit(self.options[index][0])

    @on(OptionList.OptionSelected)
    def select_option(self, event):
        if event.option_list.id == "suggestions":
            self.exit(str(event.option.prompt))
        elif not self.multiple:
            self.exit(self.options[event.option_index][0])

    @on(Button.Pressed, "#back")
    def back_button(self):
        self.action_back()


class TerminalUI:
    def __init__(self, cancel_exception):
        self.cancel_exception = cancel_exception
        self.section = "Welcome"
        self.error = ""

    def run(self, question, **kwargs):
        app = PromptApp(self.section, question, error=self.error, **kwargs)
        self.error = ""
        # Inline mode preserves the native command output and removal summary in scrollback.
        result = app.run(inline=True)
        if app.interrupted:
            raise KeyboardInterrupt()
        if app.cancelled and app.cancel_value is None:
            raise self.cancel_exception()
        return result

    def select(self, question, options, default, detail="", cancel_value=None):
        return self.run(question, options=options, default=default, detail=detail, cancel_value=cancel_value)

    def field(self, question, default=""):
        return self.run(question, default=default)

    def confirm(self, question, default=False, *, yes_help="Do this now", no_help="Leave this unchanged"):
        return self.select(question, [(False, "No — " + no_help), (True, "Yes — " + yes_help)], default)

    def sections(self, steps):
        return self.run("Set up this profile", detail="Choose the sections to configure. Existing personal data is preserved.",
                        options=[(i, title) for i, (title, _) in enumerate(steps)],
                        default=[0, 2, 3, 4], multiple=True, cancel_value=[])

    def profile(self, names):
        detail = "Pick an existing profile below, or type a new name." if names else "No existing profiles. Type a name for your new profile."
        return self.run("Profile", detail=detail, names=names)

    def menu(self, setup, steps, statuses, detail=""):
        self.section = "Setup"
        title_width = max(len(title) for title, _ in steps)
        options = [("0", f"{'Guided setup':<{title_width}}  Choose the sections you want to configure")]
        for index, (title, _) in enumerate(steps, 1):
            state = statuses.get(index, "Not checked")
            color = "yellow" if any(text in state for text in (
                "missing", "blocked", "No messaging connection", "Profile not created", "Pack not installed",
                "AI not selected", "Service stopped — assistant offline")) else "dim"
            label = Text(f"{title:<{title_width}}  ")
            label.append(state, style=color)
            options.append((str(index), label))
        options.append(("q", f"{'Finish':<{title_width}}  Close setup; leave saved settings as they are"))
        subtitle = f"Profile: {setup.profile} · Connections have not been tested."
        if detail:
            subtitle += "\n" + detail
        return self.select("Make this assistant yours", options, "0", subtitle, cancel_value="q")

    def notice(self, message, error=False):
        if error:
            self.error = message
        print(message)

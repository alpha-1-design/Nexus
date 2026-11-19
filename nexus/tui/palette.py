from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static


class CommandPalette(ModalScreen[str]):
    """Modal screen for command selection."""

    BINDINGS = [("escape", "dismiss", "Dismiss")]

    COMMANDS = ["/help", "/settings", "/theme", "/skills", "/init", "/clear", "/history", "/tools", "/model", "/facts", "/session", "/plugins", "/discover", "/queue", "/exit"]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("COMMAND PALETTE", classes="panel-header"),
            OptionList(*self.COMMANDS, id="command-list"),
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.prompt))

    def action_dismiss(self) -> None:
        self.dismiss()

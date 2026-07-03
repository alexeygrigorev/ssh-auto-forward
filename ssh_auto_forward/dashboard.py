"""Interactive TUI dashboard for ssh-auto-forward."""

import logging
import threading
import webbrowser
from typing import TYPE_CHECKING, List, Optional, Set, Tuple

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.keys import Keys
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Static

if TYPE_CHECKING:
    from ssh_auto_forward.forwarder import SSHAutoForwarder


# Global buffer for logs before dashboard is mounted
_log_buffer: List[Tuple[str, int]] = []


def _human_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    if n < 1024:
        return f"{n}B"
    elif n < 1024 * 1024:
        value = n / 1024
        return f"{value:.0f}K" if value >= 10 else f"{value:.1f}K"
    elif n < 1024 * 1024 * 1024:
        value = n / (1024 * 1024)
        return f"{value:.0f}M" if value >= 10 else f"{value:.1f}M"
    else:
        value = n / (1024 * 1024 * 1024)
        return f"{value:.0f}G" if value >= 10 else f"{value:.1f}G"


def _human_speed(bps: float) -> str:
    """Format bytes/sec as human-readable speed string."""
    if bps < 1:
        return "idle"
    elif bps < 1024:
        return f"{bps:.0f}B/s"
    elif bps < 1024 * 1024:
        value = bps / 1024
        return f"{value:.0f}K/s" if value >= 10 else f"{value:.1f}K/s"
    else:
        value = bps / (1024 * 1024)
        return f"{value:.0f}M/s" if value >= 10 else f"{value:.1f}M/s"


def _compact_text(text: str, max_chars: int) -> str:
    """Truncate text for narrow table display."""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def _compact_path(path: str, max_chars: int = 18, tail_parts: int = 2) -> str:
    """Format a path for narrow table display."""
    clean_path = path.strip().replace("\\", "/").rstrip("/")
    if not clean_path:
        return "[dim]-[/dim]"

    parts = [part for part in clean_path.split("/") if part]
    if not parts:
        return clean_path

    tail = "/".join(parts[-tail_parts:])
    display = f".../{tail}" if len(parts) > tail_parts else tail
    if len(display) <= max_chars:
        return display

    name = parts[-1]
    basename_display = f".../{name}"
    if len(basename_display) <= max_chars:
        return basename_display

    return "..." + name[-(max_chars - 3) :]


class LogHandler(logging.Handler):
    """Custom logging handler that sends logs to the dashboard."""

    def __init__(self, dashboard_app: "DashboardApp" = None):
        super().__init__()
        self.dashboard = dashboard_app

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to the dashboard."""
        try:
            msg = self.format(record)
            if self.dashboard is None:
                # Buffer logs until dashboard is ready
                _log_buffer.append((msg, record.levelno))
            else:
                self.dashboard.call_from_thread(self.dashboard.add_log, msg, record.levelno)
        except Exception:
            pass


class TunnelDataTable(DataTable):
    """A DataTable widget for displaying tunnel information."""

    def __init__(self, forwarder: "SSHAutoForwarder", include_config_ports: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.forwarder = forwarder
        self.include_config_ports = include_config_ports
        self.cursor_type = "row"
        self.zebra_stripes = True

    def on_mount(self) -> None:
        """Set up the table when mounted."""
        self.add_column("")
        self.add_column("Name")
        self.add_column("Remote")
        self.add_column("Local")
        self.add_column("Process")
        self.add_column("Folder")
        self.add_column("Data")
        self.add_column("Speed", width=5)
        self.add_column("URL")
        self.refresh_data()
        self.focus()

    @staticmethod
    def _remote_port_from_row(cells: list) -> int:
        """Extract the remote port from a table row."""
        return int(str(cells[2]))

    def refresh_data(self) -> None:
        """Refresh the table data from the forwarder."""
        # Save current state
        old_cursor_row = self.cursor_row
        selected_port = None

        try:
            if old_cursor_row is not None and old_cursor_row < len(self.rows):
                # Get row key at cursor position (rows are keyed by key, not index)
                row_keys = list(self.rows.keys())
                if old_cursor_row < len(row_keys):
                    row_key = row_keys[old_cursor_row]
                    cells = self.get_row(row_key)
                    # get_row returns list of strings, not Cell objects
                    selected_port = self._remote_port_from_row(cells)
        except (IndexError, ValueError, KeyError):
            pass

        # Clear existing rows
        self.clear()

        # Get all remote ports (detected on remote)
        all_ports = dict(self.forwarder.all_remote_ports)

        # Also add ports that have LocalForward in SSH config
        for port, local_port in self.forwarder.config_local_forwards.items():
            if port not in all_ports:
                all_ports[port] = "SSH Config"

        # Sort by port number
        port_names = getattr(self.forwarder, "port_names", {})
        if not isinstance(port_names, dict):
            port_names = {}
        process_working_dirs = getattr(self.forwarder, "process_working_dirs", {})
        if not isinstance(process_working_dirs, dict):
            process_working_dirs = {}

        row_index = 0
        new_cursor_row = None
        for port in sorted(all_ports.keys()):
            process_name = all_ports[port]
            is_forwarded = port in self.forwarder.tunnels
            is_auto_eligible = port <= self.forwarder.max_auto_port
            is_config_forwarded = port in self.forwarder.config_local_forwards

            if is_forwarded:
                local_port = self.forwarder.local_port_map.get(port, port)
                local_display = str(local_port)
                url = f"http://127.0.0.1:{local_port}"
                url_display = f"[link={url}]localhost:{local_port}[/link]"
                is_remapped = port in self.forwarder.port_remappings
                if local_port != port or is_remapped:
                    local_display = f"{local_port} (→{port})"
                status = "[green]●[/green]"
                if port in self.forwarder.manual_tunnels:
                    status += "[dim]M[/dim]"

                # Traffic stats
                tunnel = self.forwarder.tunnels[port]
                stats = tunnel.get_stats()
                total_bytes = stats["bytes_sent"] + stats["bytes_received"]
                traffic_display = _human_bytes(total_bytes) if total_bytes > 0 else "-"
                total_speed = stats["send_speed"] + stats["recv_speed"]
                speed_display = _human_speed(total_speed)
            elif is_config_forwarded:
                # Port is forwarded via SSH config LocalForward (not by this tool)
                local_port = self.forwarder.config_local_forwards[port]
                local_display = str(local_port)
                url = f"http://127.0.0.1:{local_port}"
                url_display = f"[link={url}]localhost:{local_port}[/link]"
                status = "[cyan]●[/cyan]"
                traffic_display = "-"
                speed_display = "-"
            elif is_auto_eligible:
                local_port = ""
                local_display = "-"
                url_display = "-"
                status = "[dim]●[/dim]"
                traffic_display = "-"
                speed_display = "-"
            else:
                local_port = ""
                local_display = "-"
                url_display = "-"
                status = "[dim]●[/dim]"
                traffic_display = "-"
                speed_display = "-"

            proc_display = _compact_text(process_name, 14) if process_name else "[dim]unknown[/dim]"
            name = port_names.get(port, "")
            name_display = _compact_text(name, 16) if name else "[dim]-[/dim]"
            folder_display = _compact_path(process_working_dirs.get(port, ""))

            self.add_row(
                status,
                name_display,
                str(port),
                local_display,
                proc_display,
                folder_display,
                traffic_display,
                speed_display,
                url_display,
            )

            # Track row for previously selected port
            if selected_port is not None and port == selected_port:
                new_cursor_row = row_index
            row_index += 1

        # Restore cursor position - only if we found the selected port
        if new_cursor_row is not None:
            self.move_cursor(row=new_cursor_row, animate=False)
        else:
            # If selected port is gone, try to stay at the same row index
            if old_cursor_row is not None and old_cursor_row < len(self.rows):
                self.move_cursor(row=old_cursor_row, animate=False)
            elif len(self.rows) > 0:
                self.move_cursor(row=min(old_cursor_row or 0, len(self.rows) - 1), animate=False)

    def forward_selected_port(self) -> bool:
        """Forward the selected port."""
        cursor_row = self.cursor_row
        if cursor_row is not None and cursor_row < len(self.rows):
            try:
                # Get row key at cursor position (rows are keyed by key, not index)
                row_keys = list(self.rows.keys())
                if cursor_row < len(row_keys):
                    row_key = row_keys[cursor_row]
                    cells = self.get_row(row_key)
                    # get_row returns list of strings, not Cell objects
                    remote_port = self._remote_port_from_row(cells)

                # Skip ports that are forwarded via SSH config
                if remote_port in self.forwarder.config_local_forwards:
                    self.app.query_one("#status").update(
                        f"[dim]Port {remote_port} is forwarded via SSH config - cannot toggle here[/dim]"
                    )
                    return False

                if remote_port not in self.forwarder.tunnels:
                    process_name = self.forwarder.all_remote_ports.get(remote_port, "")
                    success = self.forwarder.forward_port(remote_port, process_name, manual=True)
                    if success:
                        self.refresh_data()
                        self.app.query_one("#status").update(f"[green]✓ Started forwarding port {remote_port}[/green]")
                        return True
                    else:
                        self.app.query_one("#status").update(f"[red]✗ Failed to forward port {remote_port}[/red]")
                        return False
            except (KeyError, IndexError, ValueError, AttributeError):
                pass
        return False

    def stop_selected_port(self) -> bool:
        """Stop forwarding the selected port."""
        cursor_row = self.cursor_row
        if cursor_row is not None and cursor_row < len(self.rows):
            try:
                # Get row key at cursor position (rows are keyed by key, not index)
                row_keys = list(self.rows.keys())
                if cursor_row < len(row_keys):
                    row_key = row_keys[cursor_row]
                    cells = self.get_row(row_key)
                    # get_row returns list of strings, not Cell objects
                    remote_port = self._remote_port_from_row(cells)

                if remote_port in self.forwarder.tunnels:
                    self.forwarder.stop_forwarding_port(remote_port)
                    self.refresh_data()
                    self.app.query_one("#status").update(f"[yellow]✗ Stopped forwarding port {remote_port}[/yellow]")
                    return True
            except (KeyError, IndexError, ValueError, AttributeError):
                pass
        return False

    def open_selected_url(self) -> bool:
        """Open the selected port's URL in browser."""
        cursor_row = self.cursor_row
        if cursor_row is not None and cursor_row < len(self.rows):
            try:
                # Get row key at cursor position (rows are keyed by key, not index)
                row_keys = list(self.rows.keys())
                if cursor_row < len(row_keys):
                    row_key = row_keys[cursor_row]
                    cells = self.get_row(row_key)
                    # get_row returns list of strings, not Cell objects
                    remote_port = self._remote_port_from_row(cells)

                if remote_port in self.forwarder.tunnels:
                    local_port = self.forwarder.local_port_map.get(remote_port, remote_port)
                    url = f"http://127.0.0.1:{local_port}"
                    webbrowser.open(url)
                    self.app.query_one("#status").update(f"[green]Opened {url} in browser[/green]")
                    return True
            except (KeyError, IndexError, ValueError, AttributeError):
                pass
        return False

    def toggle_selected_port(self) -> bool:
        """Toggle forwarding: start if stopped, stop if started."""
        cursor_row = self.cursor_row
        if cursor_row is not None and cursor_row < len(self.rows):
            try:
                # Get row key at cursor position
                row_keys = list(self.rows.keys())
                if cursor_row < len(row_keys):
                    row_key = row_keys[cursor_row]
                    cells = self.get_row(row_key)
                    remote_port = self._remote_port_from_row(cells)

                # Skip ports that are forwarded via SSH config (read-only)
                if remote_port in self.forwarder.config_local_forwards:
                    self.app.query_one("#status").update(
                        f"[dim]Port {remote_port} is forwarded via SSH config - cannot toggle here[/dim]"
                    )
                    return False

                if remote_port in self.forwarder.tunnels:
                    # Port is forwarded - stop it
                    self.forwarder.stop_forwarding_port(remote_port)
                    self.refresh_data()
                    self.app.query_one("#status").update(f"[yellow]✗ Stopped forwarding port {remote_port}[/yellow]")
                    return True
                else:
                    # Port is not forwarded - start it
                    process_name = self.forwarder.all_remote_ports.get(remote_port, "")
                    success = self.forwarder.forward_port(remote_port, process_name, manual=True)
                    if success:
                        self.refresh_data()
                        self.app.query_one("#status").update(f"[green]✓ Started forwarding port {remote_port}[/green]")
                        return True
                    else:
                        self.app.query_one("#status").update(f"[red]✗ Failed to forward port {remote_port}[/red]")
                        return False
            except (KeyError, IndexError, ValueError, AttributeError):
                pass
        return False


class LogPanel(Vertical):
    """A collapsible log panel."""

    def __init__(self, *children, **kwargs):
        super().__init__(*children, **kwargs)
        self._expanded = True

    def toggle(self) -> None:
        """Toggle the log panel."""
        self._expanded = not self._expanded
        self.display = self._expanded

    def on_mount(self) -> None:
        """Show by default on mount so logs are visible."""
        self.display = True


class ReconnectOverlay(Static):
    """Overlay shown when SSH connection is lost."""

    DEFAULT_CSS = """
    ReconnectOverlay {
        display: none;
        width: 100%;
        height: 100%;
        content-align: center middle;
        text-align: center;
        background: $surface 90%;
        color: $text;
        text-style: bold;
        layer: overlay;
    }
    """

    def show_countdown(self, seconds: int) -> None:
        """Show the overlay with a countdown value."""
        self.update(f"[bold red]Connection lost[/bold red]\n\nReconnecting in {seconds}...")
        self.display = True

    def show_connecting(self) -> None:
        """Show the overlay in 'connecting' state."""
        self.update("[bold yellow]Reconnecting...[/bold yellow]")
        self.display = True

    def hide(self) -> None:
        """Hide the overlay."""
        self.display = False


class InputScreen(ModalScreen):
    """A modal screen for inputting a single value."""

    DEFAULT_CSS = """
    InputScreen {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: 14;
        border: thick $primary;
        background: $surface;
        padding: 1;
    }
    #dialog Vertical {
        height: 1fr;
    }
    #title {
        text-align: center;
        text-style: bold;
        margin: 0 1 1 1;
    }
    #input {
        margin: 0 1 1 1;
    }
    #buttons {
        height: 3;
    }
    #buttons Horizontal {
        align: center middle;
        height: 1fr;
    }
    #buttons Button {
        min-width: 10;
        margin: 0 0 0 1;
    }
    #buttons Button:last-child {
        margin-right: 1;
    }
    """

    def __init__(self, title: str, prompt: str, initial: str = "", placeholder: str = "", show_reset: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.title_text = title
        self.prompt_text = prompt
        self.initial_value = initial
        self.placeholder_text = placeholder
        self.show_reset = show_reset

    def compose(self) -> ComposeResult:
        buttons = [
            Button("OK", variant="primary", id="ok"),
            Button("Cancel", id="cancel"),
        ]
        if self.show_reset:
            buttons.append(Button("Reset", id="reset"))

        yield Vertical(
            Static(self.title_text, id="title"),
            Static(self.prompt_text),
            Input(
                value=self.initial_value,
                placeholder=self.placeholder_text,
                id="input",
            ),
            Horizontal(*buttons, id="buttons"),
            id="dialog",
        )

    def on_mount(self) -> None:
        input_widget = self.query_one("#input", Input)
        input_widget.focus()
        input_widget.select_all()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            input_widget = self.query_one("#input", Input)
            self.dismiss(input_widget.value)
        elif event.button.id == "reset":
            self.dismiss("")  # Empty string signals reset
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class HostSelectorScreen(ModalScreen):
    """A modal screen for selecting an SSH host from the config."""

    DEFAULT_CSS = """
    HostSelectorScreen {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: 22;
        border: thick $primary;
        background: $surface;
        padding: 1;
    }
    #title {
        text-align: center;
        text-style: bold;
        margin: 0 1 1 1;
    }
    #host_list {
        height: 1fr;
        margin: 0 1 1 1;
    }
    #buttons {
        height: 3;
    }
    #buttons Horizontal {
        align: center middle;
        height: 1fr;
    }
    #buttons Button {
        min-width: 12;
        margin: 0 0 0 1;
    }
    #buttons Button:last-child {
        margin-right: 1;
    }
    """

    # Special row key for the toggle item
    _TOGGLE_ROW_KEY = "__toggle_show_local_forward__"

    def __init__(self, hosts: List[str], hosts_with_local_forward: List[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.hosts = hosts
        self.hosts_with_local_forward = hosts_with_local_forward or []
        self.selected_host: Optional[str] = None
        self._showing_local_forward_hosts = False

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Select SSH Host", id="title"),
            Static(
                "Use arrow keys + Enter/Space to select, or press Q to cancel",
                id="instructions",
            ),
            DataTable(id="host_list"),
            Horizontal(
                Button("Connect", variant="primary", id="connect"),
                Button("Cancel", id="cancel"),
                id="buttons",
            ),
            id="dialog",
        )

    def on_mount(self) -> None:
        """Set up the host list table."""
        table = self.query_one("#host_list", DataTable)
        table.cursor_type = "row"
        table.add_column("Host", key="host")
        table.zebra_stripes = True

        if not self.hosts and not self.hosts_with_local_forward:
            # No hosts found - hide the table and show error message
            table.display = False
            self.query_one("#instructions").update("[red]No SSH hosts found in config[/red]")
            self.query_one("#connect").disabled = True
        else:
            # Populate the list
            self._refresh_host_list()

    def _refresh_host_list(self) -> None:
        """Refresh the host list table based on current toggle state."""
        table = self.query_one("#host_list", DataTable)

        # Track if we were on the toggle row before refresh
        was_on_toggle_row = table.cursor_row is not None and table.cursor_row < len(table.rows)
        current_row_key = None
        if was_on_toggle_row:
            row_keys = list(table.rows.keys())
            if table.cursor_row < len(row_keys):
                current_row_key = row_keys[table.cursor_row]

        table.clear()

        # Add regular hosts first
        for host in sorted(self.hosts):
            table.add_row(host, key=host)

        # Track the toggle row position
        toggle_row_position = None

        # Add the toggle row if there are hosts with local forward
        if self.hosts_with_local_forward:
            if self._showing_local_forward_hosts:
                table.add_row("[dim]▼ Hide hosts with local forwards[/dim]", key=self._TOGGLE_ROW_KEY)
                toggle_row_position = len(self.hosts)  # Position after regular hosts
                # Add hosts with local forward below
                for host in sorted(self.hosts_with_local_forward):
                    table.add_row(f"{host} [dim](has LocalForward)[/dim]", key=host)
            else:
                table.add_row("[dim]▶ Show hosts with local forwards[/dim]", key=self._TOGGLE_ROW_KEY)
                toggle_row_position = len(self.hosts)  # Position after regular hosts

        table.focus()

        # If we were on the toggle row, restore focus to it
        if toggle_row_position is not None and current_row_key == self._TOGGLE_ROW_KEY:
            table.move_cursor(row=toggle_row_position, animate=False)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection in the host list."""
        if event.row_key == self._TOGGLE_ROW_KEY:
            # Toggle the show/hide state
            self._showing_local_forward_hosts = not self._showing_local_forward_hosts
            self._refresh_host_list()
        elif event.row_key:
            # Regular host selection
            # Strip any markup to get the actual hostname
            cells = self.query_one("#host_list", DataTable).get_row(event.row_key)
            host_text = str(cells[0])
            # Remove the [dim] markup if present
            if " [dim]" in host_text:
                host_text = host_text.split(" [dim]")[0]
            self.selected_host = host_text
            # Auto-dismiss when a row is selected
            self.dismiss(self.selected_host)

    def on_key(self, event: events.Key) -> None:
        """Handle key press events."""
        # Handle Space key for selection
        if event.key == Keys.Space:
            event.stop()
            table = self.query_one("#host_list", DataTable)
            cursor_row = table.cursor_row
            if cursor_row is not None and cursor_row < len(table.rows):
                row_keys = list(table.rows.keys())
                if cursor_row < len(row_keys):
                    row_key = row_keys[cursor_row]
                    # Trigger the same logic as row selection
                    if row_key == self._TOGGLE_ROW_KEY:
                        self._showing_local_forward_hosts = not self._showing_local_forward_hosts
                        self._refresh_host_list()
                    elif row_key:
                        cells = table.get_row(row_key)
                        host_text = str(cells[0])
                        if " [dim]" in host_text:
                            host_text = host_text.split(" [dim]")[0]
                        self.selected_host = host_text
                        self.dismiss(self.selected_host)


class HostSelectorApp(App):
    """A simple app for host selection."""

    TITLE = "ssh-auto-forward: Select Host"
    CSS = """
    Screen {
        align: center middle;
    }
    """

    def __init__(self, hosts: List[str], hosts_with_local_forward: List[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.hosts = hosts
        self.hosts_with_local_forward = hosts_with_local_forward or []
        self.selected_host: Optional[str] = None

    def on_mount(self) -> None:
        self.push_screen(HostSelectorScreen(self.hosts, self.hosts_with_local_forward), self._on_host_selected)

    def _on_host_selected(self, result: Optional[str]) -> None:
        self.selected_host = result
        self.exit()


class DashboardApp(App):
    """The main dashboard application."""

    TITLE = "ssh-auto-forward"
    CSS = """
    #logs_container {
        height: 30%;
        dock: bottom;
    }
    TunnelDataTable {
        height: 1fr;
    }
    #main_content {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("l", "toggle_logs", "Toggle logs"),
        Binding("o", "open_url", "Open URL"),
        Binding("x", "toggle_port", "Toggle port"),
        Binding("enter", "toggle_port", "Toggle port"),
        Binding("m", "remap_port", "Remap port"),
        Binding("n", "name_port", "Name port"),
    ]

    def __init__(
        self,
        forwarder: Optional["SSHAutoForwarder"] = None,
        host: str = None,
        ssh_config_path: str = None,
        skip_ports: Set = None,
        port_range: Tuple[int, int] = (3000, 10000),
        scan_interval: int = 5,
        max_auto_port: int = 10000,
        include_config_ports: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.forwarder = forwarder
        self._host = host
        self._ssh_config_path = ssh_config_path
        self._skip_ports = skip_ports or set()
        self._port_range = port_range
        self._scan_interval = scan_interval
        self._max_auto_port = max_auto_port
        self._include_config_ports = include_config_ports
        self._log_handler: LogHandler = None
        self._reconnecting = False
        self._countdown_timer = None
        self._remote_port_to_remap = None
        self._remote_port_to_name = None
        self._refresh_lock = threading.Lock()
        self._refresh_in_progress = False
        self._waiting_for_host = forwarder is None

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        yield Header()

        # Connection info - different based on whether we have a forwarder
        if self.forwarder:
            conn_text = (
                f"[bold cyan]Connected to: {self.forwarder.host_alias}[/bold cyan] | "
                f"Auto-forward ports ≤ {self.forwarder.max_auto_port}"
            )
            help_text = "Press [bold]X/Enter[/bold] toggle, [bold]O[/bold] open URL, [bold]N[/bold] name, [bold]M[/bold] remap, [bold]L[/bold] logs, [bold]Q[/bold] quit"
        else:
            conn_text = "[bold yellow]Select a host to connect...[/bold yellow]"
            help_text = "Press [bold]Q[/bold] to quit"

        yield Vertical(
            Static(conn_text, id="connection_info"),
            Static(help_text, id="help"),
            # Only show tunnel table if we have a forwarder
            TunnelDataTable(self.forwarder, include_config_ports=self._include_config_ports, id="tunnels_table")
            if self.forwarder
            else Static("Please select a host...", id="placeholder"),
            Static("", id="status"),
            LogPanel(
                Static("[bold]Logs[/bold] (press L to close)", id="logs_title"),
                RichLog(id="logs", markup=True, auto_scroll=True, highlight=True),
                id="logs_container",
            ),
            id="main_content",
        )
        yield ReconnectOverlay(id="reconnect_overlay")
        yield Footer()

    def on_mount(self) -> None:
        """Set up refresh timer and log handler when mounted."""
        # Only set up auto-refresh if we have a forwarder
        if self.forwarder:
            self.set_interval(5, self.auto_refresh)
        elif self._host:
            # Host was provided, create forwarder directly
            self._create_forwarder_and_connect(self._host)
        else:
            # No host provided - show host selector
            from ssh_auto_forward.forwarder import get_ssh_hosts_with_local_forward

            hosts, hosts_with_lf = get_ssh_hosts_with_local_forward(self._ssh_config_path)
            self.push_screen(HostSelectorScreen(hosts, hosts_with_lf), self._on_host_selected)

        # Set up log handler to capture logs
        self._log_handler = LogHandler(self)
        self._log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))

        # Add handler to the forwarder's logger
        logger = logging.getLogger("ssh-auto-forward")
        logger.addHandler(self._log_handler)

        # Replay any buffered logs
        global _log_buffer
        for msg, level in _log_buffer:
            self.add_log(msg, level)
        _log_buffer.clear()

    def _create_forwarder_and_connect(self, host: str) -> None:
        """Create a forwarder and connect to the specified host in background."""
        from ssh_auto_forward.forwarder import SSHAutoForwarder

        self._host = host
        self.forwarder = SSHAutoForwarder(
            host_alias=host,
            ssh_config_path=self._ssh_config_path,
            skip_ports=self._skip_ports,
            port_range=self._port_range,
            scan_interval=self._scan_interval,
            max_auto_port=self._max_auto_port,
        )

        # Update status to show we're connecting
        self.query_one("#status").update(f"[yellow]Connecting to {host}...[/yellow]")

        # Run connection in background thread so UI updates (logs) are visible
        threading.Thread(target=self._do_connect, args=(host,), daemon=True).start()

    def _do_connect(self, host: str) -> None:
        """Perform the connection in a background thread."""
        logger = logging.getLogger("ssh-auto-forward")
        logger.info(f"Connecting to {host}...")

        success = self.forwarder.connect()

        if success:
            logger.info("Connected successfully!")
            # Update UI on main thread
            self.call_from_thread(self._on_connected_success)
        else:
            logger.error(f"Failed to connect to {host}")
            # Update UI on main thread
            self.call_from_thread(self._on_connected_failed, host)

    def _on_connected_success(self) -> None:
        """Called on main thread after successful connection."""
        # Update UI
        self._update_ui_for_connected_host()
        # Start auto-refresh
        self.set_interval(5, self.auto_refresh)
        # Clear status
        self.query_one("#status").update("[green]Connected[/green]")
        # Initial scan runs in the background so the dashboard stays responsive.
        self._start_background_refresh()

    def _on_connected_failed(self, host: str) -> None:
        """Called on main thread after failed connection."""
        self.query_one("#status").update(f"[red]Failed to connect to {host}[/red]")
        # Show host selector to try another host
        from ssh_auto_forward.forwarder import get_ssh_hosts_with_local_forward

        hosts, hosts_with_lf = get_ssh_hosts_with_local_forward(self._ssh_config_path)
        self.push_screen(HostSelectorScreen(hosts, hosts_with_lf), self._on_host_selected)

    def _on_host_selected(self, result: Optional[str]) -> None:
        """Called when a host is selected from the host selector."""
        if not result:
            # User cancelled - exit the app
            self.exit()
            return

        # Use the shared connection logic
        self._create_forwarder_and_connect(result)

    def _update_ui_for_connected_host(self) -> None:
        """Update the UI after successfully connecting to a host."""
        # Update connection info
        conn_info = self.query_one("#connection_info")
        conn_info.update(
            f"[bold cyan]Connected to: {self.forwarder.host_alias}[/bold cyan] | "
            f"Auto-forward ports ≤ {self.forwarder.max_auto_port}"
        )

        # Update help text
        help_text = self.query_one("#help")
        help_text.update(
            "Press [bold]X/Enter[/bold] toggle, [bold]O[/bold] open URL, [bold]N[/bold] name, [bold]M[/bold] remap, [bold]L[/bold] logs, [bold]Q[/bold] quit"
        )

        # Replace placeholder with tunnel table
        placeholder = self.query_one("#placeholder")
        if placeholder:
            placeholder.remove()
            main_content = self.query_one("#main_content", Vertical)
            # Insert the table before the status message
            table = TunnelDataTable(self.forwarder, include_config_ports=self._include_config_ports, id="tunnels_table")
            main_content.mount(table, before=self.query_one("#status"))
            # Focus the table so arrow keys work immediately
            table.focus()

    def add_log(self, message: str, level: int) -> None:
        """Add a log message to the log widget."""
        log_widget = self.query_one("#logs", RichLog)

        # Colorize based on level
        if level >= logging.ERROR:
            message = f"[red]{message}[/red]"
        elif level >= logging.WARNING:
            message = f"[yellow]{message}[/yellow]"

        log_widget.write(message)

    def _is_connected(self) -> bool:
        """Check if the SSH connection is still alive."""
        if not self.forwarder:
            return False
        return self.forwarder._is_connected()

    def auto_refresh(self) -> None:
        """Auto-refresh the table data and check connection health."""
        self._start_background_refresh()

    def _start_background_refresh(self, show_status: bool = False) -> None:
        """Start a background scan if one is not already running."""
        if not self.forwarder or self._reconnecting:
            return

        with self._refresh_lock:
            if self._refresh_in_progress:
                return
            self._refresh_in_progress = True

        if show_status:
            self.query_one("#status").update("[dim]Refreshing...[/dim]")

        threading.Thread(target=self._do_background_refresh, args=(show_status,), daemon=True).start()

    def _do_background_refresh(self, show_status: bool = False) -> None:
        """Run connection checks and remote scans off the UI thread."""
        try:
            if not self._is_connected():
                self.call_from_thread(self._on_background_connection_lost)
                return

            self.forwarder.scan_and_forward()
            self.call_from_thread(self._on_background_refresh_done, show_status)
        except Exception as e:
            logger = logging.getLogger("ssh-auto-forward")
            logger.debug(f"Background refresh failed: {e}")
            self.call_from_thread(self._on_background_refresh_done, False)

    def _on_background_connection_lost(self) -> None:
        """Handle connection loss detected by the background refresh."""
        with self._refresh_lock:
            self._refresh_in_progress = False
        if not self._reconnecting:
            self._start_reconnect()

    def _on_background_refresh_done(self, show_status: bool = False) -> None:
        """Apply background refresh results on the UI thread."""
        with self._refresh_lock:
            self._refresh_in_progress = False

        if self._reconnecting:
            return

        try:
            table = self.query_one("#tunnels_table", TunnelDataTable)
        except Exception:
            return

        table.refresh_data()
        if show_status:
            self.query_one("#status").update("[green]⟳ Refreshed[/green]")

    def _start_reconnect(self) -> None:
        """Start the reconnection countdown loop."""
        if self._reconnecting:
            return
        self._reconnecting = True
        logger = logging.getLogger("ssh-auto-forward")
        logger.warning("SSH connection lost, will attempt to reconnect...")
        self._reconnect_countdown(5)

    def _reconnect_countdown(self, remaining: int) -> None:
        """Tick the countdown and attempt reconnect when it reaches 0."""
        overlay = self.query_one("#reconnect_overlay", ReconnectOverlay)
        if remaining > 0:
            overlay.show_countdown(remaining)
            self._countdown_timer = self.set_timer(1.0, lambda: self._reconnect_countdown(remaining - 1))
        else:
            overlay.show_connecting()
            # Run reconnect in a thread to avoid blocking the UI
            threading.Thread(target=self._do_reconnect, daemon=True).start()

    def _do_reconnect(self) -> None:
        """Attempt to reconnect (runs in background thread)."""
        logger = logging.getLogger("ssh-auto-forward")
        try:
            # Close old connection
            try:
                self.forwarder.ssh_client.close()
            except Exception:
                pass
            # Clear stale tunnels
            self.forwarder._clear_stale_state()

            success = self.forwarder.connect()
            if success:
                self.forwarder.scan_and_forward()
                self.call_from_thread(self._on_reconnect_success)
            else:
                self.call_from_thread(self._on_reconnect_failure)
        except Exception as e:
            logger.error(f"Reconnect error: {e}")
            self.call_from_thread(self._on_reconnect_failure)

    def _on_reconnect_success(self) -> None:
        """Called on the main thread when reconnection succeeds."""
        logger = logging.getLogger("ssh-auto-forward")
        logger.info("Reconnected successfully!")
        self._reconnecting = False
        overlay = self.query_one("#reconnect_overlay", ReconnectOverlay)
        overlay.hide()
        self.query_one("#status").update("[green]✓ Reconnected[/green]")
        table = self.query_one("#tunnels_table", TunnelDataTable)
        table.refresh_data()

    def _on_reconnect_failure(self) -> None:
        """Called on the main thread when reconnection fails - restart countdown."""
        self._reconnect_countdown(5)

    def action_refresh(self) -> None:
        """Refresh the table data."""
        if not self._reconnecting:
            self._start_background_refresh(show_status=True)

    def action_toggle_logs(self) -> None:
        """Toggle the log panel."""
        log_panel = self.query_one("#logs_container", LogPanel)
        log_panel.toggle()

    def action_toggle_port(self) -> None:
        """Toggle forwarding: start if stopped, stop if started."""
        table = self.query_one("#tunnels_table", TunnelDataTable)
        table.toggle_selected_port()

    def action_open_url(self) -> None:
        """Open the selected port's URL in browser."""
        table = self.query_one("#tunnels_table", TunnelDataTable)
        table.open_selected_url()

    def _selected_remote_port(self) -> Optional[int]:
        """Return the selected remote port, if any."""
        table = self.query_one("#tunnels_table", TunnelDataTable)
        cursor_row = table.cursor_row
        if cursor_row is None or cursor_row >= len(table.rows):
            return None

        try:
            row_keys = list(table.rows.keys())
            if cursor_row >= len(row_keys):
                return None
            row_key = row_keys[cursor_row]
            cells = table.get_row(row_key)
            return TunnelDataTable._remote_port_from_row(cells)
        except (KeyError, IndexError, ValueError, AttributeError):
            return None

    def action_name_port(self) -> None:
        """Name the selected port."""
        remote_port = self._selected_remote_port()
        if remote_port is None:
            self.query_one("#status").update("[red]✗ No port selected[/red]")
            return

        port_names = getattr(self.forwarder, "port_names", {})
        if not isinstance(port_names, dict):
            port_names = {}
        current_name = port_names.get(remote_port, "")
        self._remote_port_to_name = remote_port
        self.push_screen(
            InputScreen(
                title=f"Name port {remote_port}",
                prompt="Name:",
                initial=current_name,
                placeholder="e.g. admin UI, API server, database...",
                show_reset=bool(current_name),
            ),
            self._on_name_result,
        )

    def _on_name_result(self, result: str | None) -> None:
        """Handle the port name input result."""
        remote_port = self._remote_port_to_name
        if remote_port is None or result is None:
            return

        if result.strip():
            self.forwarder.set_port_name(remote_port, result)
            self.query_one("#status").update(f"[green]✓ Saved name for port {remote_port}[/green]")
        else:
            self.forwarder.clear_port_name(remote_port)
            self.query_one("#status").update(f"[green]✓ Cleared name for port {remote_port}[/green]")

        table = self.query_one("#tunnels_table", TunnelDataTable)
        table.refresh_data()

    def action_remap_port(self) -> None:
        """Remap the selected port to a specific local port."""
        remote_port = self._selected_remote_port()
        if remote_port is not None:
            try:
                # Show current remapping if exists, otherwise show current local port
                current_local = self.forwarder.port_remappings.get(remote_port)
                if current_local is None and remote_port in self.forwarder.local_port_map:
                    current_local = self.forwarder.local_port_map[remote_port]
                if current_local is None:
                    current_local = remote_port

                has_custom_remapping = remote_port in self.forwarder.port_remappings
                self._remote_port_to_remap = remote_port
                self.push_screen(
                    InputScreen(
                        title=f"Remap port {remote_port}",
                        prompt="Local port:",
                        initial=str(current_local),
                        placeholder="Enter local port number...",
                        show_reset=has_custom_remapping,
                    ),
                    self._on_remap_result,
                )
            except (KeyError, IndexError, ValueError, AttributeError):
                self.query_one("#status").update("[red]✗ No port selected[/red]")
        else:
            self.query_one("#status").update("[red]✗ No port selected[/red]")

    def _on_remap_result(self, result: str | None) -> None:
        """Handle the input screen result."""
        remote_port = self._remote_port_to_remap

        # Empty string means reset
        if result == "":
            self.forwarder.clear_port_remapping(remote_port)
            # Restart tunnel if active
            if remote_port in self.forwarder.tunnels:
                process_name = self.forwarder.process_names.get(remote_port, "")
                was_manual = remote_port in self.forwarder.manual_tunnels
                self.forwarder.stop_forwarding_port(remote_port)
                self.forwarder.forward_port(remote_port, process_name, manual=was_manual)
            self.query_one("#status").update(f"[green]✓ Port {remote_port} remapping reset[/green]")
            table = self.query_one("#tunnels_table", TunnelDataTable)
            table.refresh_data()
            return

        if result:
            try:
                local_port = int(result)

                if local_port < 1 or local_port > 65535:
                    self.query_one("#status").update("[red]✗ Port must be between 1 and 65535[/red]")
                    return

                success = self.forwarder.set_port_remapping(remote_port, local_port)
                if success:
                    self.query_one("#status").update(f"[green]✓ Port {remote_port} → local {local_port}[/green]")
                    table = self.query_one("#tunnels_table", TunnelDataTable)
                    table.refresh_data()
                else:
                    self.query_one("#status").update(f"[red]✗ Local port {local_port} is not available[/red]")
            except ValueError:
                self.query_one("#status").update("[red]✗ Invalid port number[/red]")


def run_dashboard(
    forwarder: "SSHAutoForwarder" = None,
    host: str = None,
    ssh_config_path: str = None,
    skip_ports: Set = None,
    port_range: Tuple[int, int] = (3000, 10000),
    scan_interval: int = 5,
    max_auto_port: int = 10000,
    include_config_ports: bool = False,
) -> None:
    """Run the dashboard app.

    Args:
        forwarder: Optional SSHAutoForwarder instance. If not provided, host must be specified.
        host: Optional host alias. If not provided, user will be prompted to select.
        ssh_config_path: Path to SSH config file.
        skip_ports: Ports to skip forwarding.
        port_range: Local port range to use.
        scan_interval: Scan interval in seconds.
        max_auto_port: Maximum port to auto-forward.
        include_config_ports: If True, include ports already forwarded via SSH config LocalForward.
    """
    # Set up log handler early to capture all logs (including connection logs)
    log_handler = LogHandler()  # No dashboard yet, will buffer logs
    log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    logging.getLogger("ssh-auto-forward").addHandler(log_handler)

    # Remove console handler for dashboard mode (logs go to dashboard panel only)
    logger = logging.getLogger("ssh-auto-forward")
    console_handler = None
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            console_handler = handler
            break

    try:
        if console_handler:
            logger.removeHandler(console_handler)

        # If forwarder is provided, use it (backward compatibility)
        if forwarder is not None:
            app = DashboardApp(forwarder, include_config_ports=include_config_ports)
            app.run()
            return

        # Otherwise, create app with optional host (will prompt if not provided)
        app = DashboardApp(
            forwarder=None,
            host=host,
            ssh_config_path=ssh_config_path,
            skip_ports=skip_ports,
            port_range=port_range,
            scan_interval=scan_interval,
            max_auto_port=max_auto_port,
            include_config_ports=include_config_ports,
        )
        app.run()
    finally:
        # Restore console handler
        if console_handler:
            logger.addHandler(console_handler)
        # Remove the dashboard log handler
        logger.removeHandler(log_handler)


def run_host_selector(ssh_config_path: str = None) -> Optional[str]:
    """Run the host selector and return the selected host.

    Args:
        ssh_config_path: Path to SSH config file.

    Returns:
        The selected host name, or None if cancelled.
    """
    from ssh_auto_forward.forwarder import get_ssh_hosts

    hosts = get_ssh_hosts(ssh_config_path)

    if not hosts:
        logger = logging.getLogger("ssh-auto-forward")
        logger.error("No SSH hosts found in config file")
        return None

    app = HostSelectorApp(hosts)
    app.run()
    return app.selected_host

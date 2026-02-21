"""Interactive TUI dashboard for ssh-auto-forward."""

import logging
import webbrowser
from typing import TYPE_CHECKING, List, Tuple

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, DataTable, RichLog
from textual.containers import Vertical
from textual.binding import Binding

if TYPE_CHECKING:
    from ssh_auto_forward.forwarder import SSHAutoForwarder


# Global buffer for logs before dashboard is mounted
_log_buffer: List[Tuple[str, int]] = []


def _human_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    else:
        return f"{n / (1024 * 1024 * 1024):.1f} GB"


def _human_speed(bps: float) -> str:
    """Format bytes/sec as human-readable speed string."""
    if bps < 1:
        return "idle"
    elif bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    else:
        return f"{bps / (1024 * 1024):.1f} MB/s"


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
                self.dashboard.add_log(msg, record.levelno)
        except Exception:
            pass


class TunnelDataTable(DataTable):
    """A DataTable widget for displaying tunnel information."""

    def __init__(self, forwarder: "SSHAutoForwarder", **kwargs):
        super().__init__(**kwargs)
        self.forwarder = forwarder
        self.cursor_type = "row"
        self.zebra_stripes = True

    def on_mount(self) -> None:
        """Set up the table when mounted."""
        self.add_columns("Remote Port", "Local Port", "Process", "Status", "Traffic", "Speed", "URL")
        self.refresh_data()

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
                    selected_port = int(str(cells[0]))
        except (IndexError, ValueError, KeyError):
            pass

        # Clear existing rows
        self.clear()

        # Get all remote ports (detected on remote)
        all_ports = dict(self.forwarder.all_remote_ports)

        # Sort by port number
        row_index = 0
        new_cursor_row = None
        for port in sorted(all_ports.keys()):
            process_name = all_ports[port]
            is_forwarded = port in self.forwarder.tunnels
            is_auto_eligible = port <= self.forwarder.max_auto_port

            if is_forwarded:
                local_port = self.forwarder.local_port_map.get(port, port)
                local_display = str(local_port)
                url = f"http://127.0.0.1:{local_port}"
                url_display = f"[link={url}]localhost:{local_port}[/link]"
                if local_port != port:
                    local_display = f"{local_port} (→{port})"
                status = "[green]● Forwarded[/green]"
                if port in self.forwarder.manual_tunnels:
                    status += " [dim]([bold]manual[/bold])[/dim]"

                # Traffic stats
                tunnel = self.forwarder.tunnels[port]
                stats = tunnel.get_stats()
                total_bytes = stats["bytes_sent"] + stats["bytes_received"]
                traffic_display = _human_bytes(total_bytes) if total_bytes > 0 else "-"
                total_speed = stats["send_speed"] + stats["recv_speed"]
                speed_display = _human_speed(total_speed)
            elif is_auto_eligible:
                local_port = ""
                local_display = "-"
                url_display = "-"
                status = "[dim]● Available[/dim]"
                traffic_display = "-"
                speed_display = "-"
            else:
                local_port = ""
                local_display = "-"
                url_display = "-"
                status = "[dim grey58]○ High port (press Enter)[/dim grey58]"
                traffic_display = "-"
                speed_display = "-"

            proc_display = process_name if process_name else "[dim]unknown[/dim]"

            self.add_row(
                str(port), local_display, proc_display, status,
                traffic_display, speed_display, url_display,
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
                    remote_port = int(str(cells[0]))

                if remote_port not in self.forwarder.tunnels:
                    process_name = self.forwarder.all_remote_ports.get(remote_port, "")
                    success = self.forwarder.forward_port(remote_port, process_name, manual=True)
                    if success:
                        self.refresh_data()
                        self.app.query_one("#status").update(
                            f"[green]✓ Started forwarding port {remote_port}[/green]"
                        )
                        return True
                    else:
                        self.app.query_one("#status").update(
                            f"[red]✗ Failed to forward port {remote_port}[/red]"
                        )
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
                    remote_port = int(str(cells[0]))

                if remote_port in self.forwarder.tunnels:
                    self.forwarder.stop_forwarding_port(remote_port)
                    self.refresh_data()
                    self.app.query_one("#status").update(
                        f"[yellow]✗ Stopped forwarding port {remote_port}[/yellow]"
                    )
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
                    remote_port = int(str(cells[0]))

                if remote_port in self.forwarder.tunnels:
                    local_port = self.forwarder.local_port_map.get(remote_port, remote_port)
                    url = f"http://127.0.0.1:{local_port}"
                    webbrowser.open(url)
                    self.app.query_one("#status").update(
                        f"[green]Opened {url} in browser[/green]"
                    )
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
                    remote_port = int(str(cells[0]))

                if remote_port in self.forwarder.tunnels:
                    # Port is forwarded - stop it
                    self.forwarder.stop_forwarding_port(remote_port)
                    self.refresh_data()
                    self.app.query_one("#status").update(
                        f"[yellow]✗ Stopped forwarding port {remote_port}[/yellow]"
                    )
                    return True
                else:
                    # Port is not forwarded - start it
                    process_name = self.forwarder.all_remote_ports.get(remote_port, "")
                    success = self.forwarder.forward_port(remote_port, process_name, manual=True)
                    if success:
                        self.refresh_data()
                        self.app.query_one("#status").update(
                            f"[green]✓ Started forwarding port {remote_port}[/green]"
                        )
                        return True
                    else:
                        self.app.query_one("#status").update(
                            f"[red]✗ Failed to forward port {remote_port}[/red]"
                        )
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
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("l", "toggle_logs", "Toggle logs"),
        Binding("o", "open_url", "Open URL"),
        Binding("x", "toggle_port", "Toggle port"),
        Binding("enter", "toggle_port", "Toggle port"),
    ]

    def __init__(self, forwarder: "SSHAutoForwarder", **kwargs):
        super().__init__(**kwargs)
        self.forwarder = forwarder
        self._log_handler: LogHandler = None

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        yield Header()
        yield Vertical(
            Static(
                f"[bold cyan]Connected to: {self.forwarder.host_alias}[/bold cyan] | "
                f"Auto-forward ports ≤ {self.forwarder.max_auto_port}",
                id="connection_info",
            ),
            Static("Press [bold]X/Enter[/bold] to toggle (open/close), [bold]O[/bold] to open URL, [bold]L[/bold] for logs, [bold]Q[/bold] to quit", id="help"),
            TunnelDataTable(self.forwarder, id="tunnels_table"),
            Static("", id="status"),
            LogPanel(
                Static("[bold]Logs[/bold] (press L to close)", id="logs_title"),
                RichLog(id="logs", markup=True, auto_scroll=True, highlight=True),
                id="logs_container",
            ),
        )
        yield Footer()

    def on_mount(self) -> None:
        """Set up refresh timer and log handler when mounted."""
        self.set_interval(5, self.auto_refresh)

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

    def add_log(self, message: str, level: int) -> None:
        """Add a log message to the log widget."""
        log_widget = self.query_one("#logs", RichLog)

        # Colorize based on level
        if level >= logging.ERROR:
            message = f"[red]{message}[/red]"
        elif level >= logging.WARNING:
            message = f"[yellow]{message}[/yellow]"

        log_widget.write(message)

    def auto_refresh(self) -> None:
        """Auto-refresh the table data."""
        table = self.query_one("#tunnels_table", TunnelDataTable)
        table.refresh_data()

    def action_refresh(self) -> None:
        """Refresh the table data."""
        self.auto_refresh()
        self.query_one("#status").update("[green]⟳ Refreshed[/green]")

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


def run_dashboard(forwarder: "SSHAutoForwarder") -> None:
    """Run the dashboard app."""
    app = DashboardApp(forwarder)
    app.run()

"""Interactive TUI dashboard for ssh-auto-forward."""

import logging
import threading
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
        self.add_columns("Remote", "Local", "Process", "Status", "Traffic", "Speed", "URL")
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
                status = "[dim]● Available[/dim]"
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
    ]

    def __init__(self, forwarder: "SSHAutoForwarder", **kwargs):
        super().__init__(**kwargs)
        self.forwarder = forwarder
        self._log_handler: LogHandler = None
        self._reconnecting = False
        self._countdown_timer = None

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
            id="main_content",
        )
        yield ReconnectOverlay(id="reconnect_overlay")
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

    def _is_connected(self) -> bool:
        """Check if the SSH connection is still alive."""
        try:
            transport = self.forwarder.ssh_client.get_transport()
            if transport is None or not transport.is_active():
                return False
            transport.send_ignore()
            return True
        except Exception:
            return False

    def auto_refresh(self) -> None:
        """Auto-refresh the table data and check connection health."""
        if self._reconnecting:
            return
        if not self._is_connected():
            self._start_reconnect()
            return
        table = self.query_one("#tunnels_table", TunnelDataTable)
        table.refresh_data()

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
            self.forwarder.tunnels.clear()
            self.forwarder.local_port_map.clear()
            self.forwarder.process_names.clear()
            self.forwarder.manual_tunnels.clear()
            self.forwarder.failed_ports.clear()
            self.forwarder.all_remote_ports.clear()

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
            table = self.query_one("#tunnels_table", TunnelDataTable)
            table.refresh_data()
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

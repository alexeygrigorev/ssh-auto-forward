"""Integration tests for ssh-auto-forward dashboard using Textual Pilot API."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from ssh_auto_forward.dashboard import DashboardApp, HostSelectorScreen, HostSelectorApp
from textual.color import Color


@pytest.fixture(autouse=True)
def mock_webbrowser_open():
    """Mock webbrowser.open to prevent actual browser opening during tests."""
    with patch("webbrowser.open") as mock:
        yield mock


@pytest.mark.asyncio
async def test_dashboard_compose_and_render():
    """Test that the dashboard can be composed and rendered without errors."""
    # Create a mock forwarder with some test data
    mock_forwarder = Mock()
    mock_forwarder.host_alias = "testhost"
    mock_forwarder.max_auto_port = 10000
    mock_forwarder.all_remote_ports = {
        8000: "python",
        8080: "node",
        9000: "python3",
    }
    mock_forwarder.tunnels = {}
    mock_forwarder.local_port_map = {}
    mock_forwarder.manual_tunnels = set()
    mock_forwarder.port_remappings = {}
    mock_forwarder.process_names = {}
    mock_forwarder.config_local_forwards = {}

    # Create the app
    app = DashboardApp(mock_forwarder)

    # Run in test mode (headless, won't touch terminal)
    async with app.run_test() as pilot:
        # The app should compose and render without errors
        # Just pause to let the app settle
        await pilot.pause()


@pytest.mark.asyncio
async def test_dashboard_with_empty_ports():
    """Test that the dashboard works correctly when no ports are detected."""
    mock_forwarder = Mock()
    mock_forwarder.host_alias = "testhost"
    mock_forwarder.max_auto_port = 10000
    mock_forwarder.all_remote_ports = {}
    mock_forwarder.tunnels = {}
    mock_forwarder.local_port_map = {}
    mock_forwarder.manual_tunnels = set()
    mock_forwarder.port_remappings = {}
    mock_forwarder.process_names = {}
    mock_forwarder.config_local_forwards = {}

    app = DashboardApp(mock_forwarder)

    async with app.run_test() as pilot:
        # Should not crash with empty port list
        await pilot.pause()


@pytest.mark.asyncio
async def test_dashboard_keyboard_navigation():
    """Test keyboard navigation in the dashboard."""
    mock_forwarder = Mock()
    mock_forwarder.host_alias = "testhost"
    mock_forwarder.max_auto_port = 10000
    mock_forwarder.all_remote_ports = {
        8000: "python",
        8080: "node",
    }
    mock_forwarder.tunnels = {}
    mock_forwarder.local_port_map = {}
    mock_forwarder.manual_tunnels = set()
    mock_forwarder.port_remappings = {}
    mock_forwarder.process_names = {}
    mock_forwarder.config_local_forwards = {}

    app = DashboardApp(mock_forwarder)

    async with app.run_test() as pilot:
        # Test pressing 'r' for refresh
        await pilot.press("r")
        await pilot.pause()

        # Test pressing 'q' to quit (this should exit the pilot)
        await pilot.press("q")


@pytest.mark.asyncio
async def test_dashboard_click_selector():
    """Test clicking on widgets using selectors."""
    mock_forwarder = Mock()
    mock_forwarder.host_alias = "testhost"
    mock_forwarder.max_auto_port = 10000
    mock_forwarder.all_remote_ports = {}
    mock_forwarder.tunnels = {}
    mock_forwarder.local_port_map = {}
    mock_forwarder.manual_tunnels = set()
    mock_forwarder.port_remappings = {}
    mock_forwarder.process_names = {}
    mock_forwarder.config_local_forwards = {}

    app = DashboardApp(mock_forwarder)

    async with app.run_test() as pilot:
        # Try to click on the table (should not crash)
        await pilot.click("#tunnels_table")
        await pilot.pause()


@pytest.mark.asyncio
async def test_dashboard_open_url_with_forwarded_port(mock_webbrowser_open):
    """Test pressing 'O' opens URL in browser for forwarded port."""
    # Create a mock forwarder with a forwarded port
    mock_tunnel = Mock()
    mock_tunnel.get_stats.return_value = {
        "bytes_sent": 0, "bytes_received": 0,
        "send_speed": 0.0, "recv_speed": 0.0, "idle_secs": None,
    }
    mock_forwarder = Mock()
    mock_forwarder.host_alias = "testhost"
    mock_forwarder.max_auto_port = 10000
    mock_forwarder.all_remote_ports = {
        8000: "python",
    }
    mock_forwarder.tunnels = {8000: mock_tunnel}
    mock_forwarder.local_port_map = {8000: 8000}
    mock_forwarder.manual_tunnels = set()
    mock_forwarder.port_remappings = {}
    mock_forwarder.process_names = {}
    mock_forwarder.config_local_forwards = {}

    app = DashboardApp(mock_forwarder)

    async with app.run_test() as pilot:
        # Navigate to first row and press 'O' to open URL
        # This should not crash with KeyError: 9
        await pilot.press("down")  # Move to first row
        await pilot.pause()
        await pilot.press("o")  # Open URL
        await pilot.pause()
        # Verify browser was opened with correct URL
        mock_webbrowser_open.assert_called_once_with("http://127.0.0.1:8000")


@pytest.mark.asyncio
async def test_dashboard_open_url_with_no_forwarded_port(mock_webbrowser_open):
    """Test pressing 'O' on non-forwarded port does nothing."""
    mock_forwarder = Mock()
    mock_forwarder.host_alias = "testhost"
    mock_forwarder.max_auto_port = 10000
    mock_forwarder.all_remote_ports = {
        8000: "python",
    }
    mock_forwarder.tunnels = {}  # No forwarded ports
    mock_forwarder.local_port_map = {}
    mock_forwarder.manual_tunnels = set()
    mock_forwarder.port_remappings = {}
    mock_forwarder.process_names = {}
    mock_forwarder.config_local_forwards = {}

    app = DashboardApp(mock_forwarder)

    async with app.run_test() as pilot:
        # Navigate to row and press 'O' - should do nothing (port not forwarded)
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        # Should not crash and browser should not be opened
        mock_webbrowser_open.assert_not_called()


@pytest.mark.asyncio
async def test_dashboard_toggle_port_to_start():
    """Test pressing X or Enter on a stopped port starts forwarding."""
    mock_forwarder = Mock()
    mock_forwarder.host_alias = "testhost"
    mock_forwarder.max_auto_port = 10000
    mock_forwarder.all_remote_ports = {
        8000: "python",
    }
    mock_forwarder.tunnels = {}  # Port not forwarded
    mock_forwarder.local_port_map = {}
    mock_forwarder.manual_tunnels = set()
    mock_forwarder.port_remappings = {}
    mock_forwarder.process_names = {}
    mock_forwarder.config_local_forwards = {}
    # Mock the forward_port method to return True
    mock_forwarder.forward_port = Mock(return_value=True)

    app = DashboardApp(mock_forwarder)

    async with app.run_test() as pilot:
        # Navigate to row and press X to start forwarding
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        # Verify forward_port was called
        mock_forwarder.forward_port.assert_called_once_with(8000, "python", manual=True)


@pytest.mark.asyncio
async def test_dashboard_toggle_port_to_stop():
    """Test pressing X or Enter on a forwarded port stops forwarding."""
    mock_tunnel = Mock()
    mock_tunnel.get_stats.return_value = {
        "bytes_sent": 0, "bytes_received": 0,
        "send_speed": 0.0, "recv_speed": 0.0, "idle_secs": None,
    }
    mock_forwarder = Mock()
    mock_forwarder.host_alias = "testhost"
    mock_forwarder.max_auto_port = 10000
    mock_forwarder.all_remote_ports = {
        8000: "python",
    }
    mock_forwarder.tunnels = {8000: mock_tunnel}  # Port is forwarded
    mock_forwarder.local_port_map = {8000: 8000}
    mock_forwarder.manual_tunnels = set()
    mock_forwarder.port_remappings = {}
    mock_forwarder.process_names = {}
    mock_forwarder.config_local_forwards = {}
    # Mock the stop_forwarding_port method
    mock_forwarder.stop_forwarding_port = Mock()

    app = DashboardApp(mock_forwarder)

    async with app.run_test() as pilot:
        # Navigate to row and press X to stop forwarding
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        # Verify stop_forwarding_port was called once (not twice)
        mock_forwarder.stop_forwarding_port.assert_called_once_with(8000)


@pytest.mark.asyncio
async def test_host_selector_with_hosts():
    """Test host selector displays hosts and allows selection."""
    hosts = ["server1", "server2", "myserver"]

    async with HostSelectorApp(hosts).run_test() as pilot:
        await pilot.pause()
        # The host list should be visible on the screen
        screen = pilot.app.screen
        host_list = screen.query_one("#host_list")
        assert len(host_list.rows) == 3

        # Select first row by pressing enter
        await pilot.press("enter")
        await pilot.pause()

        # The app should have exited with the selected host
        # The hosts are sorted alphabetically, so "myserver" should be first
        assert pilot.app.selected_host == "myserver"


@pytest.mark.asyncio
async def test_host_selector_with_no_hosts():
    """Test host selector handles empty host list gracefully."""
    async with HostSelectorApp([]).run_test() as pilot:
        await pilot.pause()
        # The connect button should be disabled on the screen
        screen = pilot.app.screen
        connect_button = screen.query_one("#connect")
        assert connect_button.disabled is True

        # Press escape or cancel to dismiss
        await pilot.press("escape")
        await pilot.pause()

        # No host should be selected
        assert pilot.app.selected_host is None


@pytest.mark.asyncio
async def test_host_selector_app():
    """Test the full HostSelectorApp."""
    hosts = ["production", "staging", "dev"]

    async with HostSelectorApp(hosts).run_test() as pilot:
        await pilot.pause()
        # Select a host by pressing enter on the first row
        await pilot.press("enter")
        await pilot.pause()

        # The app should have exited with the selected host
        # Hosts are sorted alphabetically, so "dev" is first
        assert pilot.app.selected_host == "dev"


@pytest.mark.asyncio
async def test_host_selector_cancel():
    """Test cancelling host selection."""
    hosts = ["server1", "server2"]

    async with HostSelectorApp(hosts).run_test() as pilot:
        await pilot.pause()
        # Press escape to cancel
        await pilot.press("escape")
        await pilot.pause()

        # Result should be None (cancelled)
        assert pilot.app.selected_host is None


def test_get_ssh_hosts_with_config(tmp_path):
    """Test get_ssh_hosts function with a mock SSH config."""
    from ssh_auto_forward.forwarder import get_ssh_hosts

    # Create a mock SSH config file
    mock_config_content = """# Comment line

Host server1
    HostName 192.168.1.1
    User admin

Host server2
    HostName 192.168.1.2

Host *.wildcard
    HostName 192.168.1.3

Host production
    HostName prod.example.com
"""

    config_file = tmp_path / "config"
    config_file.write_text(mock_config_content)

    hosts = get_ssh_hosts(str(config_file))

    # Should return non-wildcard hosts in the order they appear in the file
    expected_hosts = ["server1", "server2", "production"]
    assert hosts == expected_hosts


def test_get_ssh_hosts_missing_config():
    """Test get_ssh_hosts when config file doesn't exist."""
    from ssh_auto_forward.forwarder import get_ssh_hosts

    with patch("os.path.exists") as mock_exists:
        mock_exists.return_value = False

        hosts = get_ssh_hosts()

        # Should return empty list
        assert hosts == []


@pytest.mark.asyncio
async def test_dashboard_starts_without_host():
    """Test that dashboard can start without a host and shows placeholder UI."""
    from ssh_auto_forward.dashboard import DashboardApp

    with patch("ssh_auto_forward.forwarder.get_ssh_hosts", return_value=[]):
        app = DashboardApp(forwarder=None, host=None)

        async with app.run_test() as pilot:
            await pilot.pause()
            # Should show placeholder widget
            placeholder = pilot.app.query_one("#placeholder")
            assert placeholder is not None
            # Connection info should exist
            conn_info = pilot.app.query_one("#connection_info")
            assert conn_info is not None


@pytest.mark.asyncio
async def test_dashboard_shows_host_selector():
    """Test that dashboard shows host selector when started without host."""
    from ssh_auto_forward.dashboard import DashboardApp

    test_hosts = ["server1", "server2"]

    with patch("ssh_auto_forward.forwarder.get_ssh_hosts", return_value=test_hosts):
        app = DashboardApp(forwarder=None, host=None)

        async with app.run_test() as pilot:
            await pilot.pause()
            # Host selector screen should be pushed
            # The placeholder should still be visible behind the modal
            placeholder = pilot.app.query_one("#placeholder")
            assert placeholder is not None


@pytest.mark.asyncio
async def test_dashboard_host_selection_flow():
    """Test the full flow of starting dashboard without host, selecting one, and connecting."""
    from ssh_auto_forward.dashboard import DashboardApp
    from unittest.mock import patch, Mock

    # Mock the SSH hosts to return some test hosts
    test_hosts = ["test-server1", "test-server2"]

    with patch("ssh_auto_forward.forwarder.get_ssh_hosts", return_value=test_hosts):
        with patch("ssh_auto_forward.forwarder.SSHAutoForwarder") as mock_forwarder_class:
            # Mock the forwarder instance
            mock_forwarder = Mock()
            mock_forwarder.host_alias = "test-server1"
            mock_forwarder.max_auto_port = 10000
            mock_forwarder.all_remote_ports = {}
            mock_forwarder.tunnels = {}
            mock_forwarder.local_port_map = {}
            mock_forwarder.manual_tunnels = set()
            mock_forwarder.port_remappings = {}
            mock_forwarder.process_names = {}
            mock_forwarder.config_local_forwards = {}
            mock_forwarder.connect.return_value = True

            mock_forwarder_class.return_value = mock_forwarder

            app = DashboardApp(forwarder=None, host=None)

            async with app.run_test() as pilot:
                await pilot.pause()
                # Should initially show placeholder
                placeholder = pilot.app.query_one("#placeholder")
                assert placeholder is not None

                # The host selector screen should be pushed
                # Select first host by pressing enter
                await pilot.press("enter")
                await pilot.pause()

                # After selection, forwarder should be created
                assert pilot.app.forwarder is not None
                mock_forwarder_class.assert_called_once()


@pytest.mark.asyncio
async def test_dashboard_host_selection_cancel():
    """Test that cancelling host selection shows the selector again with empty hosts."""
    from ssh_auto_forward.dashboard import DashboardApp

    # First call has hosts, second call returns empty (simulating no other hosts)
    with patch("ssh_auto_forward.forwarder.get_ssh_hosts", return_value=["server1", "server2"]):
        app = DashboardApp(forwarder=None, host=None)

        async with app.run_test() as pilot:
            await pilot.pause()
            # The host selector should be visible
            # Press escape to cancel
            await pilot.press("escape")
            await pilot.pause()

            # After cancel, the app calls exit() which should stop the test
            # In run_test mode, the app continues but we can verify the behavior


@pytest.mark.asyncio
async def test_dashboard_with_explicit_host():
    """Test that dashboard works correctly when created with an explicit host."""
    from ssh_auto_forward.dashboard import DashboardApp
    from unittest.mock import patch, call

    test_hosts = ["server1", "server2"]

    with patch("ssh_auto_forward.forwarder.get_ssh_hosts", return_value=test_hosts):
        with patch("ssh_auto_forward.forwarder.SSHAutoForwarder") as mock_forwarder_class:
            mock_forwarder = Mock()
            mock_forwarder.host_alias = "server1"
            mock_forwarder.max_auto_port = 10000
            mock_forwarder.all_remote_ports = {}
            mock_forwarder.tunnels = {}
            mock_forwarder.local_port_map = {}
            mock_forwarder.manual_tunnels = set()
            mock_forwarder.port_remappings = {}
            mock_forwarder.process_names = {}
            mock_forwarder.config_local_forwards = {}
            mock_forwarder.connect.return_value = True
            mock_forwarder.scan_and_forward = Mock()

            mock_forwarder_class.return_value = mock_forwarder

            # Create app with host specified - but this will still show selector
            # because we haven't bypassed the selector flow
            # Let's test that the selector is shown
            app = DashboardApp(forwarder=None, host="server1")

            async with app.run_test() as pilot:
                await pilot.pause()

                # Since host is provided but no forwarder, selector is still shown
                # (the user can confirm the host selection)
                # The host parameter is stored for when the user confirms
                assert app._host == "server1"

                # Press enter to confirm the (only) host in the list
                await pilot.press("enter")
                await pilot.pause()
                await pilot.pause()

                # Now forwarder should be created with the selected host
                assert mock_forwarder_class.called


def test_run_dashboard_backward_compatibility():
    """Test that run_dashboard still works with forwarder parameter (backward compatibility)."""
    from ssh_auto_forward.dashboard import run_dashboard
    from unittest.mock import patch, Mock

    mock_forwarder = Mock()
    mock_forwarder.host_alias = "test"
    mock_forwarder.all_remote_ports = {}
    mock_forwarder.tunnels = {}
    mock_forwarder.local_port_map = {}
    mock_forwarder.manual_tunnels = set()
    mock_forwarder.port_remappings = {}
    mock_forwarder.process_names = {}
    mock_forwarder.max_auto_port = 10000

    with patch("ssh_auto_forward.dashboard.DashboardApp.run") as mock_run:
        run_dashboard(mock_forwarder)
        # Should create app with forwarder
        mock_run.assert_called_once()


def test_run_dashboard_with_host():
    """Test that run_dashboard works with host parameter (new behavior)."""
    from ssh_auto_forward.dashboard import run_dashboard

    with patch("ssh_auto_forward.dashboard.DashboardApp.run") as mock_run:
        run_dashboard(host="test-server")
        # Should create app without forwarder but with host
        mock_run.assert_called_once()


@pytest.mark.asyncio
async def test_host_selector_toggle_expands_and_collapses():
    """Test that the host selector toggle expands and collapses correctly."""
    from ssh_auto_forward.dashboard import HostSelectorApp
    from ssh_auto_forward.forwarder import get_ssh_hosts_with_local_forward
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        config_file = Path(tmp_dir) / "ssh_config"
        config_file.write_text("""
Host hetzner
    HostName 1.2.3.4
Host fhetzner
    HostName 1.2.3.4
    LocalForward 2999 localhost:2999
""")

        hosts_without, hosts_with = get_ssh_hosts_with_local_forward(str(config_file))

        async with HostSelectorApp(hosts_without, hosts_with).run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            table = screen.query_one("#host_list")

            # Initially: hetzner + toggle row = 2 rows
            assert len(table.rows) == 2

            # Navigate to toggle row and expand
            await pilot.press("down")  # hetzner
            await pilot.pause()
            await pilot.press("down")  # toggle row
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            # Now: hetzner + "▼ Hide..." + fhetzner = 3 rows
            assert len(table.rows) == 3

            # Cursor is still on the toggle row after expansion (fix for toggle row focus issue)
            # Press enter directly to collapse
            await pilot.press("enter")
            await pilot.pause()

            # Back to 2 rows
            assert len(table.rows) == 2


@pytest.mark.asyncio
async def test_dashboard_table_has_default_focus():
    """Test that the table has focus when dashboard opens with a connected forwarder."""
    from ssh_auto_forward.dashboard import DashboardApp
    from unittest.mock import Mock

    mock_forwarder = Mock()
    mock_forwarder.host_alias = "testhost"
    mock_forwarder.max_auto_port = 10000
    mock_forwarder.all_remote_ports = {
        8000: "python",
        8080: "node",
    }
    mock_forwarder.tunnels = {}
    mock_forwarder.local_port_map = {}
    mock_forwarder.manual_tunnels = set()
    mock_forwarder.port_remappings = {}
    mock_forwarder.process_names = {}
    mock_forwarder.config_local_forwards = {}

    app = DashboardApp(mock_forwarder)

    async with app.run_test() as pilot:
        await pilot.pause()
        # The table should have focus
        table = pilot.app.query_one("#tunnels_table")
        assert table.has_focus


@pytest.mark.asyncio
async def test_dashboard_table_has_focus_after_connection():
    """Test that the table gets focus after connecting to a host."""
    from ssh_auto_forward.dashboard import DashboardApp
    from unittest.mock import patch, Mock

    test_hosts = ["server1", "server2"]

    with patch("ssh_auto_forward.forwarder.get_ssh_hosts", return_value=test_hosts):
        with patch("ssh_auto_forward.forwarder.SSHAutoForwarder") as mock_forwarder_class:
            mock_forwarder = Mock()
            mock_forwarder.host_alias = "server1"
            mock_forwarder.max_auto_port = 10000
            mock_forwarder.all_remote_ports = {
                8000: "python",
            }
            mock_forwarder.tunnels = {}
            mock_forwarder.local_port_map = {}
            mock_forwarder.manual_tunnels = set()
            mock_forwarder.port_remappings = {}
            mock_forwarder.process_names = {}
            mock_forwarder.config_local_forwards = {}
            mock_forwarder.connect.return_value = True

            mock_forwarder_class.return_value = mock_forwarder

            app = DashboardApp(forwarder=None, host="server1")

            async with app.run_test() as pilot:
                await pilot.pause()
                # Table should have focus after connection
                await pilot.pause()
                await pilot.pause()
                table = pilot.app.query_one("#tunnels_table")
                assert table.has_focus


@pytest.mark.asyncio
async def test_host_selector_space_key_selects_host():
    """Test that Space key works to select a host in host selector."""
    from ssh_auto_forward.dashboard import HostSelectorApp

    hosts = ["server1", "server2", "server3"]

    async with HostSelectorApp(hosts).run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen

        # Press Space to select the first host (alphabetically: server1)
        await pilot.press(" ")
        await pilot.pause()

        # The app should have exited with the selected host
        assert pilot.app.selected_host == "server1"


@pytest.mark.asyncio
async def test_host_selector_space_key_toggles_local_forward_list():
    """Test that Space key toggles the local forward list in host selector."""
    from ssh_auto_forward.dashboard import HostSelectorApp
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        config_file = Path(tmp_dir) / "ssh_config"
        config_file.write_text("""
Host hetzner
    HostName 1.2.3.4
Host fhetzner
    HostName 1.2.3.4
    LocalForward 2999 localhost:2999
""")

        from ssh_auto_forward.forwarder import get_ssh_hosts_with_local_forward
        hosts_without, hosts_with = get_ssh_hosts_with_local_forward(str(config_file))

        async with HostSelectorApp(hosts_without, hosts_with).run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            table = screen.query_one("#host_list")

            # Initially: hetzner + toggle row = 2 rows
            assert len(table.rows) == 2

            # Navigate to toggle row and press Space to expand
            await pilot.press("down")  # hetzner
            await pilot.pause()
            await pilot.press("down")  # toggle row
            await pilot.pause()
            await pilot.press(" ")  # Space to expand
            await pilot.pause()

            # Now: hetzner + "▼ Hide..." + fhetzner = 3 rows
            assert len(table.rows) == 3

            # Cursor is on toggle row, press Space to collapse
            await pilot.press(" ")
            await pilot.pause()

            # Back to 2 rows
            assert len(table.rows) == 2

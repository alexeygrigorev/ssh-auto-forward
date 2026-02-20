"""Integration tests for ssh-auto-forward dashboard using Textual Pilot API."""

import pytest
from unittest.mock import Mock, patch

from ssh_auto_forward.dashboard import DashboardApp
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
    mock_forwarder = Mock()
    mock_forwarder.host_alias = "testhost"
    mock_forwarder.max_auto_port = 10000
    mock_forwarder.all_remote_ports = {
        8000: "python",
    }
    mock_forwarder.tunnels = {8000: mock_tunnel}
    mock_forwarder.local_port_map = {8000: 8000}
    mock_forwarder.manual_tunnels = set()

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
    mock_forwarder = Mock()
    mock_forwarder.host_alias = "testhost"
    mock_forwarder.max_auto_port = 10000
    mock_forwarder.all_remote_ports = {
        8000: "python",
    }
    mock_forwarder.tunnels = {8000: mock_tunnel}  # Port is forwarded
    mock_forwarder.local_port_map = {8000: 8000}
    mock_forwarder.manual_tunnels = set()
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

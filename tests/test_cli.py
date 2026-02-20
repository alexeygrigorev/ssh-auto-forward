"""Unit tests for ssh-auto-forward CLI."""

import socket
from unittest.mock import Mock, MagicMock, patch

import pytest

from ssh_auto_forward.cli import SSHAutoForwarder, SSHTunnel, DEFAULT_SKIP_PORTS


class TestSSHTunnel:
    """Tests for SSHTunnel class."""

    def test_tunnel_initialization(self):
        """Test tunnel initialization."""
        ssh_client = Mock()
        tunnel = SSHTunnel(ssh_client, "localhost", 8080, 3000)

        assert tunnel.ssh_client == ssh_client
        assert tunnel.remote_host == "localhost"
        assert tunnel.remote_port == 8080
        assert tunnel.local_port == 3000
        assert tunnel.active is False

    def test_tunnel_start_success(self):
        """Test successful tunnel start."""
        ssh_client = Mock()
        transport = Mock()
        ssh_client.get_transport.return_value = transport

        tunnel = SSHTunnel(ssh_client, "localhost", 8080, 3000)

        # Mock socket and server socket
        with patch('ssh_auto_forward.cli.socket.socket') as mock_socket_class:
            mock_server_socket = Mock()
            mock_server_socket.bind.return_value = None
            mock_server_socket.listen.return_value = None
            mock_server_socket.settimeout.return_value = None
            mock_socket_class.return_value = mock_server_socket

            result = tunnel.start()

            assert result is True
            assert tunnel.active is True
            mock_server_socket.bind.assert_called_once_with(("127.0.0.1", 3000))
            mock_server_socket.listen.assert_called_once_with(5)

    def test_tunnel_start_port_in_use(self):
        """Test tunnel start when port is already in use."""
        ssh_client = Mock()
        transport = Mock()
        ssh_client.get_transport.return_value = transport

        tunnel = SSHTunnel(ssh_client, "localhost", 8080, 3000)

        # Mock socket to raise OSError (port in use)
        with patch('ssh_auto_forward.cli.socket.socket') as mock_socket_class:
            mock_socket = Mock()
            mock_socket_class.return_value = mock_socket
            mock_socket.bind.side_effect = OSError("Address already in use")

            result = tunnel.start()

            assert result is False
            assert tunnel.active is False

    def test_tunnel_stop(self):
        """Test tunnel stop."""
        ssh_client = Mock()
        transport = Mock()
        ssh_client.get_transport.return_value = transport

        tunnel = SSHTunnel(ssh_client, "localhost", 8080, 3000)
        tunnel.active = True
        tunnel.server_socket = Mock()

        tunnel.stop()

        assert tunnel.active is False
        tunnel.server_socket.close.assert_called_once()


class TestPortAvailability:
    """Tests for port availability checking."""

    def test_find_available_local_port_first_try(self):
        """Test finding available port on first try."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.local_port_map = {}

        with patch.object(forwarder, 'is_local_port_available', return_value=True):
            port = forwarder.find_available_local_port(8080)
            assert port == 8080

    def test_find_available_local_port_with_increment(self):
        """Test finding available port with increment."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.local_port_map = {}

        # 8080 is busy, 8081 is free
        def mock_available(port):
            return port == 8081

        with patch.object(forwarder, 'is_local_port_available', side_effect=mock_available):
            port = forwarder.find_available_local_port(8080)
            assert port == 8081

    def test_find_available_local_port_from_fallback(self):
        """Test finding available port from fallback range."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.local_port_map = {}
        forwarder.port_range = (3000, 10000)

        # All high ports busy, 3000 is free
        def mock_available(port):
            return port == 3000

        with patch.object(forwarder, 'is_local_port_available', side_effect=mock_available):
            port = forwarder.find_available_local_port(50000)
            assert port == 3000

    def test_find_available_local_port_none_found(self):
        """Test when no available port is found."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.local_port_map = {}

        with patch.object(forwarder, 'is_local_port_available', return_value=False):
            port = forwarder.find_available_local_port(8080)
            assert port is None

    def test_is_local_port_available_free(self):
        """Test port availability check for free port."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.local_port_map = {}

        with patch('ssh_auto_forward.cli.socket.socket') as mock_socket_class:
            mock_socket = MagicMock()
            mock_socket.__enter__ = Mock(return_value=mock_socket)
            mock_socket.__exit__ = Mock(return_value=False)
            mock_socket.bind.return_value = None
            mock_socket_class.return_value = mock_socket

            result = forwarder.is_local_port_available(8080)
            assert result is True

    def test_is_local_port_available_in_use_by_socket(self):
        """Test port availability check when port is in use by socket."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.local_port_map = {}

        with patch('ssh_auto_forward.cli.socket.socket') as mock_socket_class:
            mock_socket = MagicMock()
            mock_socket.__enter__ = Mock(return_value=mock_socket)
            mock_socket.__exit__ = Mock(return_value=False)
            mock_socket.bind.side_effect = OSError("Address already in use")
            mock_socket_class.return_value = mock_socket

            result = forwarder.is_local_port_available(8080)
            assert result is False

    def test_is_local_port_available_in_use_by_tunnel(self):
        """Test port availability check when port is used by another tunnel."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.local_port_map = {9999: 8080}  # Remote 9999 -> local 8080

        result = forwarder.is_local_port_available(8080)
        assert result is False


class TestPortForwarding:
    """Tests for port forwarding logic."""

    def test_forward_port_already_forwarded(self):
        """Test forwarding a port that's already forwarded."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.tunnels = {8080: Mock()}

        result = forwarder.forward_port(8080)
        assert result is True

    def test_forward_port_in_skip_list(self):
        """Test forwarding a port that's in the skip list."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.skip_ports = {22, 80, 443}

        result = forwarder.forward_port(22)
        assert result is False

    def test_forward_port_uses_our_local_port(self):
        """Test that we skip remote ports that match our local forwarding ports."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.tunnels = {}
        forwarder.local_port_map = {9999: 3000}  # We're using local 3000

        result = forwarder.forward_port(3000)
        assert result is False

    def test_forward_port_failed_previously(self):
        """Test forwarding a port that failed previously."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.failed_ports = {8080}

        result = forwarder.forward_port(8080)
        assert result is False

    def test_forward_port_success(self):
        """Test successful port forwarding."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.tunnels = {}
        forwarder.local_port_map = {}
        forwarder.failed_ports = set()

        with patch.object(forwarder, 'find_available_local_port', return_value=8080):
            with patch('ssh_auto_forward.cli.SSHTunnel') as mock_tunnel_class:
                mock_tunnel = Mock()
                mock_tunnel.start.return_value = True
                mock_tunnel_class.return_value = mock_tunnel

                result = forwarder.forward_port(8080, "testproc")

                assert result is True
                assert 8080 in forwarder.tunnels
                assert forwarder.local_port_map[8080] == 8080

    def test_forward_port_failure(self):
        """Test port forwarding when tunnel start fails."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.tunnels = {}
        forwarder.local_port_map = {}
        forwarder.failed_ports = set()

        with patch.object(forwarder, 'find_available_local_port', return_value=8080):
            with patch('ssh_auto_forward.cli.SSHTunnel') as mock_tunnel_class:
                mock_tunnel = Mock()
                mock_tunnel.start.return_value = False
                mock_tunnel_class.return_value = mock_tunnel

                result = forwarder.forward_port(8080)

                assert result is False
                assert 8080 in forwarder.failed_ports

    def test_forward_port_no_available_local_port(self):
        """Test forwarding when no local port is available."""
        forwarder = SSHAutoForwarder("testhost")
        forwarder.tunnels = {}
        forwarder.local_port_map = {}
        forwarder.failed_ports = set()

        with patch.object(forwarder, 'find_available_local_port', return_value=None):
            result = forwarder.forward_port(8080)

            assert result is False
            assert 8080 in forwarder.failed_ports

    def test_stop_forwarding_port(self):
        """Test stopping forwarding for a port."""
        forwarder = SSHAutoForwarder("testhost")
        mock_tunnel = Mock()
        forwarder.tunnels = {8080: mock_tunnel}
        forwarder.local_port_map = {8080: 8080}
        forwarder.failed_ports = {8080}

        forwarder.stop_forwarding_port(8080)

        assert 8080 not in forwarder.tunnels
        assert 8080 not in forwarder.local_port_map
        assert 8080 not in forwarder.failed_ports
        mock_tunnel.stop.assert_called_once()


class TestDefaultSkipPorts:
    """Tests for default skip ports configuration."""

    def test_default_skip_ports_includes_well_known_ports(self):
        """Test that default skip ports includes well-known ports."""
        assert 22 in DEFAULT_SKIP_PORTS  # SSH
        assert 80 in DEFAULT_SKIP_PORTS  # HTTP
        assert 443 in DEFAULT_SKIP_PORTS  # HTTPS

    def test_default_skip_ports_is_all_under_1000(self):
        """Test that all default skip ports are under 1000."""
        for port in DEFAULT_SKIP_PORTS:
            assert port < 1000, f"Port {port} should be >= 1000"

    def test_default_skip_ports_is_complete_range(self):
        """Test that default skip ports includes all ports from 0-999."""
        assert len(DEFAULT_SKIP_PORTS) == 1000
        for port in range(1000):
            assert port in DEFAULT_SKIP_PORTS


class TestSSHConfigParsing:
    """Tests for SSH config parsing."""

    def test_load_ssh_config_basic(self, tmp_path):
        """Test basic SSH config loading."""
        config_file = tmp_path / "ssh_config"
        config_file.write_text("""
Host testhost
    HostName example.com
    User testuser
    Port 2222
""")

        forwarder = SSHAutoForwarder("testhost", ssh_config_path=str(config_file))

        assert forwarder.config["hostname"] == "example.com"
        assert forwarder.config["user"] == "testuser"
        assert forwarder.config["port"] == 2222

    def test_load_ssh_config_with_wildcard(self, tmp_path):
        """Test SSH config with wildcard pattern."""
        config_file = tmp_path / "ssh_config"
        config_file.write_text("""
Host *.example.com
    HostName wildcard.com
    User wildcarduser
""")

        forwarder = SSHAutoForwarder("specific.example.com", ssh_config_path=str(config_file))

        # The hostname is set to the matched wildcard host
        assert forwarder.config["hostname"] == "wildcard.com"
        assert forwarder.config["user"] == "wildcarduser"

    def test_load_ssh_config_identity_file(self, tmp_path):
        """Test SSH config with identity file."""
        config_file = tmp_path / "ssh_config"
        config_file.write_text("""
Host testhost
    IdentityFile ~/.ssh/test_key
""")

        forwarder = SSHAutoForwarder("testhost", ssh_config_path=str(config_file))

        assert "test_key" in forwarder.config["identityfile"]

    def test_load_ssh_config_defaults(self, tmp_path):
        """Test SSH config with defaults when host not found."""
        config_file = tmp_path / "ssh_config"
        config_file.write_text("""
Host other
    HostName other.com
""")

        forwarder = SSHAutoForwarder("testhost", ssh_config_path=str(config_file))

        assert forwarder.config["hostname"] == "testhost"
        assert forwarder.config["port"] == 22

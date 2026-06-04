"""Core SSH forwarding logic."""

import logging
import os
import re
import socket
import struct
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

import paramiko
from paramiko import SSHClient

logger = logging.getLogger("ssh-auto-forward")

# Ports to skip by default (well-known ports < 1000)
DEFAULT_SKIP_PORTS = set(range(0, 1000))
# Maximum port to auto-forward by default
DEFAULT_MAX_AUTO_PORT = 10000


class SSHTunnel:
    """Represents a single SSH tunnel (forwarded port)."""

    def __init__(self, ssh_client: SSHClient, remote_host: str, remote_port: int, local_port: int, process_name: str = ""):
        self.ssh_client = ssh_client
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.local_port = local_port
        self.process_name = process_name
        self.transport = ssh_client.get_transport()
        self.server_socket = None
        self.forward_thread = None
        self.active = False

        # Traffic monitoring
        self.bytes_sent = 0  # bytes sent to remote (upstream)
        self.bytes_received = 0  # bytes received from remote (downstream)
        self.last_activity = 0.0  # timestamp of last data transfer
        self._prev_bytes_sent = 0
        self._prev_bytes_received = 0
        self._prev_snapshot_time = 0.0

    def start(self):
        """Start the port forwarding in a background thread."""
        try:
            # Create a local server socket
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind(("127.0.0.1", self.local_port))
            self.server_socket.listen(5)
            self.server_socket.settimeout(1.0)  # Non-blocking accept

            self.active = True
            self.forward_thread = threading.Thread(
                target=self._forward_loop, daemon=True, name=f"Tunnel-{self.local_port}->{self.remote_port}"
            )
            self.forward_thread.start()
            logger.debug(f"✓ Tunnel active: localhost:{self.local_port} -> {self.remote_host}:{self.remote_port}")
            return True
        except Exception as e:
            logger.error(f"Failed to start tunnel for port {self.remote_port}: {e}")
            if self.server_socket:
                self.server_socket.close()
                self.server_socket = None
            return False

    def _forward_loop(self):
        """Main loop that accepts connections and forwards them."""
        while self.active:
            try:
                client_sock, addr = self.server_socket.accept()
                threading.Thread(
                    target=self._handler,
                    args=(client_sock,),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.active:
                    logger.debug(f"Error accepting connection: {e}")

    def _handler(self, client_sock):
        """Handle a single forwarded connection."""
        chan = None
        try:
            # Open a direct-tcpip channel through SSH
            chan = self.transport.open_channel(
                "direct-tcpip",
                (self.remote_host, self.remote_port),
                ("127.0.0.1", 0),
            )
            # Pipe data between client socket and SSH channel
            self._pipe(client_sock, chan)
        except Exception as e:
            logger.debug(f"Connection error: {e}")
        finally:
            try:
                client_sock.close()
            except Exception:
                pass
            try:
                if chan:
                    chan.close()
            except Exception:
                pass

    def _pipe(self, sock, chan):
        """Pipe data between socket and SSH channel using blocking I/O with threads.

        Each direction is independent: when one side reaches EOF it performs a
        graceful half-close on the destination (shutdown of the write side only)
        and exits, while the other direction keeps running until it too reaches
        EOF. This avoids truncating in-flight transfers (e.g. a large HTTP
        download) by ensuring data already handed to the kernel send buffer is
        drained before the connection is torn down, instead of being reset by an
        abrupt full close. A fatal error on either side aborts both.
        """
        import queue

        # Set once the connection is fully done (remote closed, or a fatal
        # error). The upload direction watches this so it does not block
        # forever on a client that keeps a keep-alive socket open after the
        # remote side has gone away. A clean client-side EOF does NOT set this:
        # the remote may still be streaming the response (half-duplex HTTP).
        done_event = threading.Event()
        error_queue = queue.Queue()

        def forward_socket_to_channel():
            """Forward data from socket (client) to SSH channel (remote)."""
            try:
                sock.settimeout(1.0)
                while not done_event.is_set():
                    try:
                        data = sock.recv(65536)
                    except socket.timeout:
                        continue  # Idle connection, keep waiting / re-check done
                    if not data:
                        # Client closed its write side: signal EOF to the remote
                        # so the remote server sees the end of the request body.
                        # Keep this thread alive (looping on done_event) so the
                        # download direction is never interrupted.
                        try:
                            chan.shutdown_write()
                        except Exception:
                            pass
                        while not done_event.is_set():
                            time.sleep(0.05)
                        break
                    chan.sendall(data)
                    self.bytes_sent += len(data)
                    self.last_activity = time.monotonic()
            except Exception as e:
                error_queue.put(("sock_to_chan", e))
                done_event.set()

        def forward_channel_to_socket():
            """Forward data from SSH channel (remote) to socket (client)."""
            try:
                while True:
                    # Blocking recv - Paramiko will handle the internals.
                    data = chan.recv(65536)
                    if not data:
                        # Remote closed its write side (e.g. full HTTP response
                        # body sent). Flush our pending bytes to the client and
                        # signal EOF without discarding the kernel send buffer.
                        try:
                            sock.shutdown(socket.SHUT_WR)
                        except Exception:
                            pass
                        break
                    sock.sendall(data)
                    self.bytes_received += len(data)
                    self.last_activity = time.monotonic()
            except Exception as e:
                error_queue.put(("chan_to_sock", e))
            finally:
                # The remote half of the connection is finished (clean or
                # error). Release the upload thread.
                done_event.set()

        # Start both threads
        t1 = threading.Thread(target=forward_socket_to_channel, daemon=True)
        t2 = threading.Thread(target=forward_channel_to_socket, daemon=True)
        t1.start()
        t2.start()

        # Wait for both directions to finish. There is intentionally no
        # wall-clock cap here: a slow but healthy large transfer must not be
        # killed mid-stream. Stale/broken connections are still bounded by the
        # SSH transport keepalive (see connect()).
        t1.join()
        t2.join()

        # Log any errors
        try:
            while not error_queue.empty():
                direction, error = error_queue.get_nowait()
                logger.debug(f"[_pipe] {direction}: {error}")
        except queue.Empty:
            pass

        # Cleanup. SO_LINGER makes close() block until queued data is flushed
        # (with a bounded timeout) so the final bytes of a large download reach
        # the client instead of being discarded by an abrupt reset.
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 10))
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass
        try:
            chan.close()
        except Exception:
            pass

    def get_stats(self):
        """Return current traffic stats and compute recent speed."""
        now = time.monotonic()
        dt = now - self._prev_snapshot_time if self._prev_snapshot_time else 0.0

        if dt > 0:
            send_speed = (self.bytes_sent - self._prev_bytes_sent) / dt
            recv_speed = (self.bytes_received - self._prev_bytes_received) / dt
        else:
            send_speed = 0.0
            recv_speed = 0.0

        self._prev_bytes_sent = self.bytes_sent
        self._prev_bytes_received = self.bytes_received
        self._prev_snapshot_time = now

        idle_secs = now - self.last_activity if self.last_activity else None
        return {
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "send_speed": send_speed,
            "recv_speed": recv_speed,
            "idle_secs": idle_secs,
        }

    def stop(self):
        """Stop the tunnel."""
        self.active = False
        try:
            if self.server_socket:
                self.server_socket.close()
        except Exception as e:
            logger.debug(f"Error closing server socket: {e}")
        proc_suffix = f" ({self.process_name})" if self.process_name else ""
        logger.info(f"✗ Tunnel stopped: localhost:{self.local_port} -> {self.remote_host}:{self.remote_port}{proc_suffix}")


def _find_ssh_config() -> str:
    """Find the SSH config file.

    Checks SSH_CONFIG environment variable first, then standard paths.
    """
    # Check SSH_CONFIG environment variable (like ssh does)
    env_config = os.getenv("SSH_CONFIG")
    if env_config and os.path.exists(env_config):
        return env_config

    home = os.path.expanduser("~")
    for path in [".ssh/config", ".ssh/config.d/*"]:
        full_path = os.path.join(home, path)
        if os.path.exists(full_path):
            return full_path
    return os.path.join(home, ".ssh/config")


def get_ssh_hosts(ssh_config_path: Optional[str] = None, exclude_with_local_forward: bool = True) -> List[str]:
    """Get all host aliases from SSH config file.

    Args:
        ssh_config_path: Path to SSH config file. If None, uses default path.
        exclude_with_local_forward: If True, exclude hosts that have LocalForward directives.

    Returns:
        List of host aliases found in SSH config.
    """
    if ssh_config_path is None:
        ssh_config_path = _find_ssh_config()

    hosts = []
    hosts_with_local_forward = set()

    if not os.path.exists(ssh_config_path):
        return hosts

    # Simple SSH config parser to extract Host directives
    with open(ssh_config_path, "r") as f:
        current_host = None
        has_local_forward = False

        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue

            key, value = parts[0].lower(), parts[1]

            if key == "host":
                # Save previous host if it had LocalForward
                if current_host and has_local_forward:
                    hosts_with_local_forward.add(current_host)
                # Start new host
                if "*" in value or "?" in value:
                    current_host = None
                    has_local_forward = False
                else:
                    current_host = value
                    has_local_forward = False
            elif current_host:
                if key == "localforward":
                    has_local_forward = True
                # Other keys don't affect the LocalForward status

        # Don't forget the last host
        if current_host and has_local_forward:
            hosts_with_local_forward.add(current_host)

    # Collect hosts (excluding those with LocalForward if requested)
    for host in hosts_with_local_forward:
        if not exclude_with_local_forward:
            hosts.append(host)

    # Second pass to get hosts without LocalForward
    with open(ssh_config_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue

            key, value = parts[0].lower(), parts[1]

            if key == "host":
                # Skip wildcard patterns
                if "*" in value or "?" in value:
                    continue
                # Only add if not already added (hosts with LocalForward were handled above)
                if value not in hosts_with_local_forward:
                    hosts.append(value)

    return hosts


def get_ssh_hosts_with_local_forward(ssh_config_path: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """Get host aliases from SSH config, separating hosts with and without LocalForward.

    Args:
        ssh_config_path: Path to SSH config file. If None, uses default path.

    Returns:
        Tuple of (hosts_without_local_forward, hosts_with_local_forward)
    """
    if ssh_config_path is None:
        ssh_config_path = _find_ssh_config()

    hosts_without: List[str] = []
    hosts_with: List[str] = []

    if not os.path.exists(ssh_config_path):
        return hosts_without, hosts_with

    # Parse the config file to get hosts and their LocalForward status
    current_host = None
    has_local_forward = False
    all_hosts = {}  # host -> has_local_forward

    with open(ssh_config_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue

            key, value = parts[0].lower(), parts[1]

            if key == "host":
                # Save previous host
                if current_host is not None:
                    all_hosts[current_host] = has_local_forward
                # Start new host
                if "*" in value or "?" in value:
                    current_host = None
                    has_local_forward = False
                else:
                    current_host = value
                    has_local_forward = False
            elif current_host:
                if key == "localforward":
                    has_local_forward = True

        # Don't forget the last host
        if current_host is not None:
            all_hosts[current_host] = has_local_forward

    # Separate into two lists
    for host, has_lf in sorted(all_hosts.items()):
        if has_lf:
            hosts_with.append(host)
        else:
            hosts_without.append(host)

    return hosts_without, hosts_with


class SSHAutoForwarder:
    """Main class that manages SSH connection and auto port forwarding."""

    def __init__(
        self,
        host_alias: str,
        ssh_config_path: Optional[str] = None,
        skip_ports: Optional[Set[int]] = None,
        port_range: Tuple[int, int] = (3000, 10000),
        scan_interval: int = 5,
        max_auto_port: int = DEFAULT_MAX_AUTO_PORT,
    ):
        self.host_alias = host_alias
        self.ssh_config_path = ssh_config_path or self._find_ssh_config()
        self.skip_ports = skip_ports or DEFAULT_SKIP_PORTS
        self.port_range = port_range
        self.scan_interval = scan_interval
        self.max_auto_port = max_auto_port

        self.ssh_client = None
        self.tunnels: Dict[int, SSHTunnel] = {}  # remote_port -> tunnel
        self.local_port_map: Dict[int, int] = {}  # remote_port -> local_port
        self.process_names: Dict[int, str] = {}  # remote_port -> process name
        self.failed_ports: Set[int] = set()  # Ports that failed to forward
        self.running = False
        self.next_alt_port = port_range[0]
        self.all_remote_ports: Dict[int, str] = {}  # All detected ports (including high ones)
        self.manual_tunnels: Set[int] = set()  # Ports manually forwarded (above max_auto_port)
        self.port_remappings: Dict[int, int] = {}  # Manual remote -> local port remappings

        # Get connection details
        self.config = self._load_ssh_config(host_alias)

        # Track ports that are already forwarded via SSH config LocalForward
        self.config_local_forwards: Dict[int, int] = self.config.get("local_forwards", {})

    def _find_ssh_config(self) -> str:
        """Find the SSH config file.

        Checks SSH_CONFIG environment variable first, then standard paths.
        """
        return _find_ssh_config()

    def _load_ssh_config(self, host_alias: str) -> dict:
        """Load configuration for a host from SSH config."""
        config = {
            "hostname": host_alias,
            "user": os.getenv("USER") or os.getenv("USERNAME"),
            "port": 22,
            "identityfile": None,
            "local_forwards": {},  # remote_port -> local_port mapping from LocalForward
        }

        if not os.path.exists(self.ssh_config_path):
            logger.warning(f"SSH config not found at {self.ssh_config_path}, using defaults")
            return config

        # Parse SSH config (simple parser)
        current_host = None
        host_pattern = None

        with open(self.ssh_config_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Handle multi-line values (rare)
                parts = line.split(maxsplit=1)
                if len(parts) < 2:
                    continue

                key, value = parts[0].lower(), parts[1]

                if key == "host":
                    if current_host is not None and self._host_matches(current_host, host_alias):
                        break
                    # Convert SSH glob pattern to regex
                    host_pattern = value.replace(".", r"\.").replace("*", ".*").replace("?", ".")
                    current_host = value
                elif current_host and re.match(host_pattern, host_alias):
                    if key == "hostname":
                        config["hostname"] = value
                    elif key == "user":
                        config["user"] = value
                    elif key == "port":
                        config["port"] = int(value)
                    elif key == "identityfile":
                        # Remove quotes if present
                        config["identityfile"] = value.strip('"').strip("'")
                    elif key == "localforward":
                        # Parse LocalForward: [bind_address:]port host:hostport
                        # e.g., "LocalForward 8080 localhost:8080" or "LocalForward 3000 localhost:3000"
                        parts = value.split()
                        if len(parts) >= 2:
                            try:
                                # Extract local port (first number)
                                local_port_str = parts[0]
                                # Handle bind_address:port format
                                if ":" in local_port_str:
                                    local_port_str = local_port_str.split(":")[-1]
                                local_port = int(local_port_str)

                                # Extract remote port from host:hostport
                                remote_part = parts[1]
                                if ":" in remote_part:
                                    remote_port = int(remote_part.split(":")[-1])
                                    # Store mapping: remote_port -> local_port
                                    config["local_forwards"][remote_port] = local_port
                                    logger.debug(f"Found LocalForward: remote port {remote_port} -> local port {local_port}")
                            except (ValueError, IndexError):
                                logger.debug(f"Could not parse LocalForward: {value}")

        logger.info(f"Loaded config for '{host_alias}': {config['user']}@{config['hostname']}:{config['port']}")
        return config

    def _host_matches(self, pattern: str, host: str) -> bool:
        """Check if a host pattern matches the target host.

        For literal host names (no wildcards), require an exact match.
        For patterns with wildcards (* or ?), use regex matching.
        """
        # If pattern has no wildcards, require exact match
        if "*" not in pattern and "?" not in pattern:
            return pattern == host
        # Otherwise, use regex matching for wildcard patterns
        regex = pattern.replace(".", r"\.").replace("*", ".*").replace("?", ".")
        return re.match(regex, host) is not None

    def _load_keys(self, key_path: str) -> list:
        """Load a private key file, trying different key formats."""
        keys = []
        key_types = [
            paramiko.RSAKey,
            paramiko.ECDSAKey,
            paramiko.Ed25519Key,
        ]

        for key_type in key_types:
            try:
                key = key_type.from_private_key_file(key_path)
                keys.append(key)
                logger.debug(f"Loaded key type: {key_type.__name__}")
                break
            except Exception:
                pass

        return keys

    def _get_agent_keys(self) -> list:
        """Get keys from SSH agent."""
        keys = []
        try:
            agent = paramiko.AgentRequestHandler()
            keys = agent.get_keys()
            logger.debug(f"Found {len(keys)} key(s) in SSH agent")
        except Exception as e:
            logger.debug(f"SSH agent not available: {e}")
        return keys

    def _find_identity_keys(self) -> list:
        """Find identity keys from SSH config or default locations."""
        keys = []

        # Try identityfile from SSH config first
        if self.config.get("identityfile"):
            key_path = os.path.expanduser(self.config["identityfile"])
            if os.path.exists(key_path):
                keys.extend(self._load_keys(key_path))

        # Try default key locations if no keys found yet
        if not keys:
            default_keys = [
                "~/.ssh/id_rsa",
                "~/.ssh/id_ed25519",
                "~/.ssh/id_ecdsa",
                "~/.ssh/id_ecdsa2",
                "~/.ssh/id_dsa",
            ]
            for key_path in default_keys:
                expanded = os.path.expanduser(key_path)
                if os.path.exists(expanded):
                    loaded = self._load_keys(expanded)
                    if loaded:
                        keys.extend(loaded)
                        logger.debug(f"Loaded key from {key_path}")
                        break

        return keys

    def connect(self) -> bool:
        """Establish SSH connection to the remote host."""
        self.ssh_client = SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            logger.info(f"Connecting to {self.config['hostname']}...")

            # Prepare connection parameters
            connect_kwargs = {
                "hostname": self.config["hostname"],
                "port": self.config["port"],
                "username": self.config["user"],
                "timeout": 10,
                "allow_agent": False,  # We'll handle agent manually
            }

            # Method 1: Try SSH agent keys first
            agent_keys = self._get_agent_keys()
            if agent_keys:
                logger.info(f"Trying SSH agent authentication ({len(agent_keys)} key(s))...")
                connect_kwargs["pkey"] = agent_keys[0]

            # Method 2: Try identity file keys
            if not agent_keys:
                identity_keys = self._find_identity_keys()
                if identity_keys:
                    logger.info("Trying identity file authentication...")
                    connect_kwargs["pkey"] = identity_keys[0]
                else:
                    # No keys found, let SSH client try with allow_agent=True
                    logger.info("Trying with default authentication...")
                    connect_kwargs["allow_agent"] = True

            self.ssh_client.connect(**connect_kwargs)
            transport = self.ssh_client.get_transport()
            if transport:
                keepalive_interval = int(os.environ.get("SSH_KEEPALIVE_INTERVAL", "15"))
                transport.set_keepalive(keepalive_interval)
            logger.info("✓ Connected!")
            return True

        except paramiko.AuthenticationException:
            logger.error("Authentication failed. Check your SSH keys or credentials.")
            logger.info("Hint: Ensure your SSH key is loaded in ssh-agent or your SSH config is correct.")
            return False
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    def get_remote_listening_ports(self) -> Dict[int, str]:
        """Get the list of listening ports on the remote server with process names."""
        try:
            # Try to get port + process name
            commands = [
                "ss -tlnp 2>/dev/null | awk 'NR>1 {print $4, $7}'",
                "netstat -tlnp 2>/dev/null | awk 'NR>1 && /LISTEN/ {print $4, $7}'",
            ]

            for cmd in commands:
                stdin, stdout, stderr = self.ssh_client.exec_command(cmd)
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()

                if error and "permission denied" in error.lower():
                    continue  # Try next command

                ports = {}
                for line in output.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        # Extract port from address (e.g., "0.0.0.0:2999")
                        addr = parts[0]
                        if ":" in addr:
                            port_str = addr.split(":")[-1]
                            if port_str.isdigit():
                                port = int(port_str)
                                # Extract process name (e.g., "users:((\"mdtohtml-watch\",pid=518168,fd=4))")
                                proc_info = parts[1]
                                # Try to extract process name from various formats
                                proc_name = "unknown"
                                if 'users:("' in proc_info:
                                    proc_name = proc_info.split('users:("')[1].split('"')[0]
                                elif 'users:(("' in proc_info:
                                    proc_name = proc_info.split('users:(("')[1].split('"')[0]
                                elif "/" in proc_info:
                                    proc_name = proc_info.split("/")[-1].split(",")[0]
                                ports[port] = proc_name

                if ports:
                    return ports

            # Fallback: just get ports without process names
            commands_fallback = [
                "ss -tln 2>/dev/null | awk 'NR>1 {print $4}'",
                "netstat -tln 2>/dev/null | awk 'NR>1 && /LISTEN/ {print $4}'",
            ]

            for cmd in commands_fallback:
                stdin, stdout, stderr = self.ssh_client.exec_command(cmd)
                output = stdout.read().decode().strip()

                ports = {}
                for line in output.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    # Extract port from address (e.g., "0.0.0.0:8080" or "*:8080")
                    if ":" in line:
                        port_str = line.split(":")[-1]
                        if port_str.isdigit():
                            ports[int(port_str)] = ""
                if ports:
                    logger.debug(f"Detected ports using fallback: {cmd.split()[0]}")
                    return ports

            return {}

        except Exception as e:
            logger.debug(f"Error getting remote ports: {e}")
            return {}

    def is_local_port_available(self, port: int) -> bool:
        """Check if a local port is available."""
        # Check if already used for SSH forwarding
        if port in self.local_port_map.values():
            return False

        # Check if port can be bound by a socket. Use SO_REUSEADDR to match how
        # the tunnel's listener is actually bound in start(); otherwise a port
        # left in TIME_WAIT (e.g. by a just-closed forwarded connection) is
        # falsely reported busy and we needlessly remap to port+1.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False

    def find_available_local_port(self, preferred_port: int) -> Optional[int]:
        """Find an available local port, preferring the preferred port."""
        # Try the preferred port first
        if self.is_local_port_available(preferred_port):
            return preferred_port

        # Increment until we find a free port
        for offset in range(1, 1000):
            alt_port = preferred_port + offset
            if alt_port > 65535:
                break  # Port number too high
            if self.is_local_port_available(alt_port):
                return alt_port

        # If still no luck, try from port 3000 upwards
        for port in range(3000, 65535):
            if self.is_local_port_available(port):
                return port

        return None

    def forward_port(self, remote_port: int, process_name: str = "", manual: bool = False, local_port: int = None) -> bool:
        """Create a tunnel for a remote port.

        Args:
            remote_port: The remote port to forward
            process_name: Name of the process using the port
            manual: If True, this was manually triggered (bypasses max_auto_port limit)
            local_port: Specific local port to use (overrides automatic selection)
        """
        if remote_port in self.tunnels:
            return True  # Already forwarded

        if remote_port in self.skip_ports:
            logger.debug(f"Skipping port {remote_port} (in skip list)")
            return False

        # Skip ports that are already forwarded via SSH config LocalForward
        if remote_port in self.config_local_forwards:
            local_port = self.config_local_forwards[remote_port]
            logger.debug(
                f"Skipping port {remote_port} (already forwarded via SSH config LocalForward to local port {local_port})"
            )
            return False

        # Skip remote ports that match our local forwarding ports - they're likely our own tunnels
        # But allow the port if it's the same as the remote port (direct forwarding)
        used_local_ports = set(self.local_port_map.values())
        if remote_port in used_local_ports and remote_port not in self.tunnels:
            logger.debug(f"Skipping port {remote_port} (already used as a local forwarding port)")
            return False

        # Skip ports that previously failed (to avoid spamming errors)
        if remote_port in self.failed_ports:
            return False

        # Use explicitly provided local_port, or check remappings, or find automatically
        if local_port is None:
            local_port = self.port_remappings.get(remote_port)
        if local_port is None:
            local_port = self.find_available_local_port(remote_port)
        if local_port is None:
            logger.warning(f"⚠ No available local port for remote port {remote_port}")
            self.failed_ports.add(remote_port)
            return False

        tunnel = SSHTunnel(
            ssh_client=self.ssh_client,
            remote_host="localhost",
            remote_port=remote_port,
            local_port=local_port,
            process_name=process_name,
        )

        if tunnel.start():
            self.tunnels[remote_port] = tunnel
            self.local_port_map[remote_port] = local_port
            self.process_names[remote_port] = process_name
            if manual or remote_port > self.max_auto_port:
                self.manual_tunnels.add(remote_port)

            proc_suffix = f" ({process_name})" if process_name else ""
            manual_suffix = " [manual]" if manual else ""
            if local_port != remote_port:
                logger.info(f"✓ Forwarding remote port {remote_port} -> local port {local_port}{proc_suffix}{manual_suffix}")
            else:
                logger.info(f"✓ Forwarding port {remote_port}{proc_suffix}{manual_suffix}")
            return True
        else:
            # Track failed ports to avoid retrying
            self.failed_ports.add(remote_port)
            return False

    def stop_forwarding_port(self, remote_port: int):
        """Stop forwarding a specific port."""
        if remote_port in self.tunnels:
            self.tunnels[remote_port].stop()
            del self.tunnels[remote_port]
            del self.local_port_map[remote_port]
            self.process_names.pop(remote_port, None)
            self.manual_tunnels.discard(remote_port)
            # Remove from failed ports so we can retry if the port comes back
            self.failed_ports.discard(remote_port)

    def set_port_remapping(self, remote_port: int, local_port: int) -> bool:
        """Set a persistent port remapping and restart the tunnel if active.

        Args:
            remote_port: The remote port to forward
            local_port: The local port to map to

        Returns:
            True if remapping was set successfully, False otherwise
        """
        # Validate local port is available
        if not self.is_local_port_available(local_port):
            return False

        self.port_remappings[remote_port] = local_port

        # If tunnel is already active, restart it with new port
        if remote_port in self.tunnels:
            process_name = self.process_names.get(remote_port, "")
            was_manual = remote_port in self.manual_tunnels
            self.stop_forwarding_port(remote_port)
            return self.forward_port(remote_port, process_name, manual=was_manual)

        return True

    def clear_port_remapping(self, remote_port: int) -> None:
        """Clear a port remapping."""
        self.port_remappings.pop(remote_port, None)

    def scan_and_forward(self):
        """Scan for new ports and set up forwarding."""
        remote_ports = self.get_remote_listening_ports()

        if not remote_ports:
            return

        # Store all detected ports for dashboard
        self.all_remote_ports = remote_ports.copy()

        logger.debug(f"Found {len(remote_ports)} listening port(s) on remote")

        # Forward new ports (only auto-forward ports <= max_auto_port)
        for port, proc_name in remote_ports.items():
            if port <= self.max_auto_port:
                self.forward_port(port, proc_name)
            # Ports > max_auto_port are shown in dashboard but not auto-forwarded

        # Stop forwarding closed ports
        current_remote_ports = set(self.tunnels.keys())
        closed_ports = current_remote_ports - set(remote_ports.keys())
        for port in closed_ports:
            proc_name = self.process_names.get(port, "")
            proc_suffix = f" ({proc_name})" if proc_name else ""
            logger.info(f"✗ Remote port {port} is no longer listening{proc_suffix}, stopping tunnel")
            self.stop_forwarding_port(port)

        # Update terminal title with status
        self._update_terminal_title()

    def _update_terminal_title(self):
        """Update terminal title with current status."""
        try:
            port_count = len(self.tunnels)
            title = f"ssh-auto-forward: {self.host_alias} ({port_count} tunnels active)"
            # ANSI escape code to set terminal title
            print(f"\033]0;{title}\007", end="", flush=True)
        except Exception:
            pass

    def _is_connected(self) -> bool:
        """Check if the SSH connection is still alive."""
        try:
            transport = self.ssh_client.get_transport()
            if transport is None or not transport.is_active():
                return False
            transport.send_ignore()
            return True
        except Exception:
            return False

    def _clear_stale_state(self):
        """Stop all tunnels and clear all forwarding state."""
        for remote_port in list(self.tunnels.keys()):
            try:
                self.tunnels[remote_port].stop()
            except Exception:
                pass
        self.tunnels.clear()
        self.local_port_map.clear()
        self.process_names.clear()
        self.manual_tunnels.clear()
        self.failed_ports.clear()
        self.all_remote_ports.clear()
        # Note: keep port_remappings across reconnects

    def _reconnect(self) -> bool:
        """Close old connection, clear state, and retry connect() with backoff.

        Backoff schedule: 5s -> 10s -> 20s -> 40s -> 60s (cap).
        Sleeps in 1-second increments so Ctrl+C is responsive.

        Returns True if reconnection succeeded, False if self.running became False.
        """
        try:
            self.ssh_client.close()
        except Exception:
            pass

        self._clear_stale_state()

        delay = 5
        while self.running:
            logger.info(f"Reconnecting in {delay}s...")
            for _ in range(delay):
                if not self.running:
                    return False
                time.sleep(1)

            if not self.running:
                return False

            logger.info("Attempting to reconnect...")
            if self.connect():
                logger.info("Reconnected successfully!")
                return True

            delay = min(delay * 2, 60)

        return False

    def run(self):
        """Main loop - connect and continuously scan for ports.

        Returns:
            True if connected successfully, False if connection failed.
        """
        if not self.connect():
            logger.error("Failed to connect. Exiting.")
            return False

        self.running = True

        try:
            logger.info("Starting port detection loop...")

            # Initial scan
            self.scan_and_forward()

            # Continuous scanning
            while self.running:
                time.sleep(self.scan_interval)

                if not self._is_connected():
                    logger.warning("SSH connection lost, will attempt to reconnect...")
                    if not self._reconnect():
                        break
                    continue

                self.scan_and_forward()

        except KeyboardInterrupt:
            logger.info("\nReceived interrupt signal, shutting down...")
        finally:
            self.shutdown()

    def run_dashboard(self):
        """Run with interactive dashboard."""
        # Set up log handler early to capture all logs (including connection logs)
        from ssh_auto_forward.dashboard import LogHandler

        log_handler = LogHandler()  # No dashboard yet, will buffer logs
        log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logging.getLogger("ssh-auto-forward").addHandler(log_handler)

        if not self.connect():
            logger.error("Failed to connect. Exiting.")
            return

        self.running = True

        # Remove console handler for dashboard mode (logs go to dashboard panel)
        console_handler = None
        for handler in logging.getLogger("ssh-auto-forward").handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                console_handler = handler
                break

        try:
            if console_handler:
                logging.getLogger("ssh-auto-forward").removeHandler(console_handler)

            # Initial scan before launching dashboard
            self.scan_and_forward()

            # Launch dashboard (this blocks)
            from ssh_auto_forward.dashboard import run_dashboard

            run_dashboard(self)

        except KeyboardInterrupt:
            logger.info("\nReceived interrupt signal, shutting down...")
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
        finally:
            # Restore console handler
            if console_handler:
                logging.getLogger("ssh-auto-forward").addHandler(console_handler)
            # Remove the dashboard log handler
            logging.getLogger("ssh-auto-forward").removeHandler(log_handler)
            self.shutdown()

    def shutdown(self):
        """Clean up all tunnels and close connection."""
        self.running = False

        logger.info("Stopping all tunnels...")
        for remote_port in list(self.tunnels.keys()):
            self.stop_forwarding_port(remote_port)

        if self.ssh_client:
            self.ssh_client.close()
            logger.info("✓ Disconnected")

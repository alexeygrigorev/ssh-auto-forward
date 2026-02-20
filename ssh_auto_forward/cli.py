"""CLI entry point for ssh-auto-forward."""

import argparse
import logging
import sys

from ssh_auto_forward.__version__ import __version__
from ssh_auto_forward.forwarder import (
    SSHAutoForwarder,
    DEFAULT_SKIP_PORTS,
    DEFAULT_MAX_AUTO_PORT,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ssh-auto-forward")


def main():
    parser = argparse.ArgumentParser(
        description="Automatically forward ports from a remote SSH server to your local machine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ssh-auto-forward myserver              # Launch dashboard (default)
  ssh-auto-forward myserver --cli        # Run in CLI mode
  ssh-auto-forward myserver -v           # Enable verbose logging
  ssh-auto-forward myserver -p 4000:9000 # Use local port range 4000-9000
  ssh-auto-forward myserver -s 22,80,443 # Skip specific ports
        """,
    )
    parser.add_argument("host", help="Host alias from SSH config or hostname")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("-i", "--interval", type=int, default=5, help="Scan interval in seconds (default: 5)")
    parser.add_argument(
        "-p",
        "--port-range",
        default="3000:10000",
        metavar="MIN:MAX",
        help="Local port range to use (default: 3000:10000)",
    )
    parser.add_argument(
        "-s",
        "--skip",
        default="",
        metavar="PORTS",
        help="Comma-separated list of ports to skip (default: all ports < 1000)",
    )
    parser.add_argument("-c", "--config", help="Path to SSH config file")
    parser.add_argument(
        "-m",
        "--max-auto-port",
        type=int,
        default=DEFAULT_MAX_AUTO_PORT,
        metavar="PORT",
        help=f"Maximum port to auto-forward (default: {DEFAULT_MAX_AUTO_PORT})",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in CLI mode instead of dashboard (for testing/special cases)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger("ssh-auto-forward").setLevel(logging.DEBUG)
        import paramiko.common
        paramiko.common.logging.basicConfig(level=paramiko.common.logging.DEBUG)

    # Parse port range
    try:
        port_min, port_max = map(int, args.port_range.split(":"))
        port_range = (port_min, port_max)
    except ValueError:
        logger.error("Invalid port range. Use format MIN:MAX (e.g., 3000:10000)")
        sys.exit(1)

    # Parse skip ports
    skip_ports = DEFAULT_SKIP_PORTS.copy()
    if args.skip:
        try:
            extra_skip = {int(p.strip()) for p in args.skip.split(",")}
            skip_ports.update(extra_skip)
        except ValueError:
            logger.error("Invalid skip ports. Use comma-separated integers (e.g., 22,80,443)")
            sys.exit(1)

    forwarder = SSHAutoForwarder(
        host_alias=args.host,
        ssh_config_path=args.config,
        skip_ports=skip_ports,
        port_range=port_range,
        scan_interval=args.interval,
        max_auto_port=args.max_auto_port,
    )

    if args.cli:
        forwarder.run()
    else:
        # Dashboard is the default
        forwarder.run_dashboard()


if __name__ == "__main__":
    main()

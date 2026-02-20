# SSH Auto Port Forwarder

Automatically detect and forward ports from a remote SSH server to your local machine. Similar to VS Code's port forwarding feature, but fully automatic.

![Dashboard](docs/dashboard.svg)

## Features

- **Interactive TUI dashboard** - View and manage tunnels in real-time
- Automatically discovers listening ports on the remote server
- Shows process names for each forwarded port
- Forwards ports to your local machine via SSH tunneling
- Handles port conflicts by finding alternative local ports
- Auto-detects new ports and starts forwarding
- Auto-detects closed ports and stops forwarding
- Reads connection details from your SSH config
- Skips well-known ports (< 1000) by default
- Configurable max auto-forward port (default: 10000)

## Dashboard Controls

| Key | Action |
|-----|--------|
| `X` / `Enter` | Toggle port (open if closed, close if opened) |
| `O` | Open URL in browser (for forwarded ports) |
| `R` | Refresh port list |
| `L` | Toggle log panel |
| `Q` | Quit |

## CLI Mode

For headless operation, use the `--cli` flag:

```bash
ssh-auto-forward hetzner --cli
```

## Installation

### With uv (recommended):

```bash
uvx ssh-auto-forward hetzner
```

### Install locally:

```bash
cd portforwards
uv sync
```

This installs the `ssh-auto-forward` command.

### Local development:

```bash
make run ARGS=hetzner
make run ARGS="hetzner -v"
```

## Usage

### Basic usage - uses host from your SSH config:

```bash
ssh-auto-forward hetzner
```

### Options:

```
-v, --verbose           Enable verbose logging
-i, --interval SECS     Scan interval in seconds (default: 5)
-p, --port-range MIN:MAX Local port range for remapping (default: 3000:10000)
-s, --skip PORTS        Comma-separated ports to skip (default: all ports < 1000)
-c, --config PATH       Path to SSH config file
--version               Show version and exit
```

### Examples:

```bash
# Scan every 3 seconds
ssh-auto-forward hetzner -i 3

# Use specific port range
ssh-auto-forward hetzner -p 4000:9000

# Skip specific ports
ssh-auto-forward hetzner -s 22,80,443

# Verbose mode
ssh-auto-forward hetzner -v
```

## How it works

1. Connects to your remote server using your SSH config
2. Runs `ss -tlnp` on the remote to find listening ports
3. Creates SSH tunnels for each discovered port
4. Continuously monitors for new/closed ports
5. Handles port conflicts on your local machine

## Status messages

```
✓ Connected!
✓ Forwarding port 2999 (python3)
✓ Forwarding port 7681 (ttyd)
✓ Forwarding remote port 19840 -> local port 3000 (node)
✗ Remote port 2999 is no longer listening, stopping tunnel
```

The terminal title also updates to show: `ssh-auto-forward: hetzner (18 tunnels active)`

## Testing

Start a test server on your remote machine:

```bash
ssh hetzner "python3 -m http.server 9999 --bind 127.0.0.1 &"
```

Then run `ssh-auto-forward hetzner` and you should see:

```
✓ Forwarding remote port 9999 -> local port 3003 (python3)
```

Access it locally:

```bash
curl http://localhost:3003/
```

## Stopping

Press `Ctrl+C` to stop the forwarder and close all tunnels.

## Requirements

- Python 3.10+
- paramiko
- textual (for TUI dashboard)
- Remote server must have `ss` or `netstat` command available

## Tests

### Unit tests (run locally, no SSH required):

```bash
make test
# or
uv run pytest tests/ -v
```

### Integration tests (require SSH access):

```bash
SSH_AUTO_FORWARD_TEST_HOST=hetzner uv run pytest tests_integration/ -v
```

The integration tests:
- Test that remote ports are forwarded to the same local port when available
- Test that ports increment by 1 when the local port is busy
- Test auto-detection of new ports
- Test auto-cleanup when remote ports close

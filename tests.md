# Testing Guide

This document outlines testing best practices for the ssh-auto-forward project, with a focus on Textual TUI testing.

## Textual Testing Best Practices

### 1. Use the Pilot API

The `Pilot` class is designed specifically for testing Textual apps headlessly. It simulates user interactions without needing a terminal.

```python
async with app.run_test() as pilot:
    await pilot.press("r")
    await pilot.pause()
```

### 2. Test Components in Isolation

Test individual widgets and screens without running the full app:

```python
@pytest.mark.asyncio
async def test_data_table():
    table = TunnelDataTable(mock_forwarder)
    async with table.run_test() as pilot:
        # Test table-specific behavior
        pass
```

### 3. Use `pilot.pause()` for Message Processing

Textual is message-driven - use `pause()` to wait for messages to be processed:

```python
await pilot.click("#button")
await pilot.pause()  # Wait for click event to propagate
```

### 4. Test User Interactions

- `pilot.press(key)` - Simulate key presses
- `pilot.click(selector)` - Click on widgets using CSS selectors
- `pilot.click(selector, offset=(x, y))` - Click at specific coordinates

Example:

```python
@pytest.mark.asyncio
async def test_dashboard_keyboard_navigation():
    app = DashboardApp(mock_forwarder)
    async with app.run_test() as pilot:
        await pilot.press("r")  # Press 'r' to refresh
        await pilot.pause()
        await pilot.press("q")  # Press 'q' to quit
```

### 5. Snapshot Testing

Use `pytest-textual-snapshot` for catching UI regressions:

```bash
uv add --dev pytest-textual-snapshot
```

```python
from textual_snapshot import snapshot

@pytest.mark.asyncio
async def test_dashboard_snapshot(snapshot):
    app = DashboardApp(mock_forwarder)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert snapshot == app
```

### 6. Access Widgets in Tests

Use `app.query_one()` to verify widget state:

```python
table = app.query_one("#tunnels_table", TunnelDataTable)
assert len(table.rows) == 3

status = app.query_one("#status", Static)
assert "Forwarding" in status.renderable
```

### 7. Mocks vs Integration Tests

- **Unit tests** (with mocks): Fine for testing individual widgets and logic in isolation
- **Integration tests** (no mocks): Should test the full flow with real components

For integration tests of the dashboard, using Mock for the forwarder object is acceptable since we're testing the UI logic, not the SSH connection itself. But for end-to-end tests, use real SSH connections or subprocess calls.

## Running Tests

```bash
# Run all tests
uv run pytest

# Run only dashboard tests
uv run pytest tests_integration/test_dashboard.py -v

# Run only integration tests
uv run pytest tests_integration/ -v

# Run with coverage
uv run pytest --cov=ssh_auto_forward
```

## Test Organization

- `tests_integration/test_dashboard.py` - Textual dashboard tests using Pilot API
- `tests_integration/test_auto_forward.py` - End-to-end tests with real SSH connections
- `tests_unit/` - Unit tests for individual functions (future)

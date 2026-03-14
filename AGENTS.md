# OpenPaws Development Guide

This file contains instructions for AI agents (and humans) working on the OpenPaws codebase.

## Project Overview

OpenPaws is a lightweight, always-on AI assistant with scheduled tasks and chat connectors.
Built on [OpenHands software-agent-sdk](https://github.com/OpenHands/software-agent-sdk).

## Development Tools Summary

| Tool | Purpose | Command |
|------|---------|---------|
| **pytest** | Testing | `pytest tests/` |
| **coverage** | Code coverage (with subprocess support) | `coverage run -m pytest && coverage combine && coverage report` |
| **ruff** | Linting + import sorting + complexity | `ruff check src/ tests/` |
| **radon** | Complexity metrics (CC, MI, Halstead) | `radon cc src/openpaws/ -a -s` |
| **xenon** | Complexity threshold enforcement | `xenon --max-absolute C src/openpaws/` |
| **check_function_length.py** | Function line count | `python scripts/check_function_length.py src/ --all` |
| **pylint** | Statement count per function | `pylint src/ --disable=all --enable=R0915` |

## Standard Development Workflow

Before committing changes, run:
```bash
# 1. Lint and auto-fix
ruff check --fix src/ tests/

# 2. Run tests with coverage
coverage run -m pytest tests/
coverage combine
coverage report --fail-under=80

# 3. Check complexity (optional but recommended)
xenon --max-absolute C --max-modules A --max-average A src/openpaws/
```

## Quick Start

```bash
# Install in development mode
pip install -e ".[dev]"

# Run all tests
pytest tests/

# Run tests with coverage (including subprocess coverage)
coverage run -m pytest tests/
coverage combine
coverage report --show-missing

# Lint code
ruff check src/ tests/

# Run the CLI
openpaws --help
openpaws status
```

## Project Structure

```
src/openpaws/
├── __init__.py      # Package version
├── __main__.py      # Entry point for `python -m openpaws`
├── cli.py           # Click CLI commands (start, stop, status, tasks)
├── config.py        # YAML config parsing with env var expansion
├── daemon.py        # Daemon process management (PID file, signals, logging)
└── scheduler.py     # Cron-based task scheduling

tests/
├── conftest.py              # Pytest config, subprocess coverage setup
├── test_config.py           # Config parsing tests
├── test_daemon.py           # Unit tests for daemon module
├── test_daemon_integration.py  # Integration tests (real process start/stop)
└── test_scheduler.py        # Scheduler unit tests
```

## Development Commands

### Testing

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_scheduler.py

# Run with verbose output
pytest tests/ -v

# Run specific test
pytest tests/test_daemon.py::TestPidFileManagement::test_write_and_read_pid_file
```

### Code Coverage

The project uses `coverage.py` with subprocess support for measuring code in forked
daemon processes.

```bash
# Full coverage workflow
coverage run -m pytest tests/
coverage combine           # Merge subprocess data files
coverage report           # Terminal report
coverage html             # HTML report in htmlcov/

# Quick check (single command, no subprocess coverage)
pytest --cov=src/openpaws tests/
```

**Note**: Integration tests spawn real daemon processes. The `conftest.py` installs
a `.pth` file that enables coverage collection in these subprocesses.

### Linting

```bash
# Check for issues (includes complexity via C90 rule)
ruff check src/ tests/

# Auto-fix issues
ruff check --fix src/ tests/
```

### Code Complexity

The project uses multiple tools to measure code complexity:

```bash
# Cyclomatic Complexity (CC) - measures decision points
# Grades: A (1-5), B (6-10), C (11-20), D (21-30), E (31-40), F (41+)
radon cc src/openpaws/ -a -s

# Maintainability Index (MI) - overall maintainability score
# Grades: A (20-100), B (10-19), C (0-9)
radon mi src/openpaws/ -s

# Raw metrics (LOC, SLOC, comments, etc.)
radon raw src/openpaws/ -s

# Halstead metrics (effort, difficulty, bugs estimate)
radon hal src/openpaws/

# Threshold check (fails CI if complexity exceeds limits)
xenon --max-absolute C --max-modules A --max-average A src/openpaws/
```

**Current Complexity Status:**
- Average CC: A (2.9)
- All modules: A maintainability
- Highest complexity: `get_daemon_status()` at C (14) - acceptable for status aggregation

**Complexity Thresholds (enforced by ruff C90 + xenon):**
- Individual functions: max complexity 15 (ruff)
- Module average: A (xenon)
- Absolute max: C (xenon)

### Running the Daemon

```bash
# Start daemon (backgrounds by default)
openpaws start

# Start with custom config
openpaws start --config /path/to/config.yaml

# Start in foreground (for debugging)
openpaws start --foreground

# Check status
openpaws status

# Stop daemon
openpaws stop
```

## Environment Variables

For testing and running multiple instances:

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENPAWS_DIR` | Base directory for all files | `~/.openpaws` |
| `OPENPAWS_PID_FILE` | Explicit PID file path | `$OPENPAWS_DIR/openpaws.pid` |
| `OPENPAWS_LOG_FILE` | Explicit log file path | `$OPENPAWS_DIR/logs/openpaws.log` |

Example for running isolated test instances:
```bash
OPENPAWS_DIR=/tmp/test1 openpaws start
OPENPAWS_DIR=/tmp/test2 openpaws start  # Second instance, no conflict
```

## Key Design Patterns

### PID File Management

The daemon uses a PID file (`~/.openpaws/openpaws.pid`) to:
- Prevent multiple instances
- Enable `stop` command to find the process
- Track uptime (via file mtime)

### Signal Handling

- `SIGTERM`: Graceful shutdown (cleanup, remove PID file)
- `SIGINT`: Same as SIGTERM (Ctrl+C in foreground mode)

### Config File

Default location: `~/.openpaws/config.yaml`

```yaml
channels:
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}  # Env var expansion

groups:
  main:
    channel: telegram
    chat_id: "123456789"
    trigger: "@paw"

tasks:
  morning-news:
    schedule: "0 8 * * *"  # Cron syntax
    group: main
    prompt: "Summarize top AI news"
```

## Testing Guidelines

### Unit Tests

- Use `pytest` fixtures for common setup
- Use `tmp_path` fixture for file-based tests
- Use `monkeypatch` for environment variables

### Integration Tests

Integration tests in `test_daemon_integration.py` actually start/stop daemon processes:

- Each test gets isolated directory via `OPENPAWS_DIR`
- Tests can run in parallel without conflicts
- Coverage is collected from subprocesses

### Mocking

Prefer real implementations over mocks. Only use mocks when:
- Testing error handling for external services
- Testing async task execution without waiting

## Current Coverage

| Module | Coverage |
|--------|----------|
| `__init__.py` | 100% |
| `__main__.py` | 100% |
| `config.py` | 96% |
| `cli.py` | 87% |
| `daemon.py` | 85% |
| `scheduler.py` | 88% |
| **Overall** | **87%** |

## Common Tasks

### Adding a New CLI Command

1. Add command to `src/openpaws/cli.py`
2. Add tests to `tests/test_cli.py` (create if needed)
3. Run `pytest tests/ && ruff check src/`

### Adding a New Config Option

1. Add field to appropriate dataclass in `src/openpaws/config.py`
2. Update parsing in `load_config()`
3. Add test in `tests/test_config.py`

### Debugging Daemon Issues

```bash
# Run in foreground to see output
openpaws start --foreground

# Check logs
cat ~/.openpaws/logs/openpaws.log

# Check if running
openpaws status
ps aux | grep openpaws
```

## GitLab CI (TODO)

When CI is configured, it should run:
```yaml
stages:
  - quality
  - test

lint:
  stage: quality
  script:
    - pip install -e ".[dev]"
    - ruff check src/ tests/

complexity:
  stage: quality
  script:
    - pip install -e ".[dev]"
    - xenon --max-absolute C --max-modules A --max-average A src/openpaws/

test:
  stage: test
  script:
    - pip install -e ".[dev]"
    - coverage run -m pytest tests/
    - coverage combine
    - coverage report --fail-under=80
```

## Quick Quality Check

Run all quality checks in one command:
```bash
ruff check src/ tests/ && \
xenon --max-absolute C --max-modules A --max-average A src/openpaws/ && \
coverage run -m pytest tests/ -q && \
coverage combine && \
coverage report
```

## Tool Reference

### ruff

Fast Python linter that replaces flake8, isort, and more.

**What it checks:**
- `E`: pycodestyle errors (PEP 8)
- `F`: pyflakes (undefined names, unused imports)
- `I`: isort (import ordering)
- `UP`: pyupgrade (Python version upgrades)
- `C90`: mccabe complexity (functions with CC > 15)

**Configuration:** `pyproject.toml` under `[tool.ruff]`

### coverage

Measures which lines of code are executed during tests.

**Key features:**
- `parallel = true`: Collects data from subprocesses (daemon forks)
- `branch = true`: Measures branch coverage, not just line coverage
- Subprocess coverage requires `COVERAGE_PROCESS_START` env var

**Workflow:**
```bash
coverage run -m pytest tests/  # Run tests, create .coverage.* files
coverage combine                # Merge subprocess data
coverage report                 # Show results
coverage html                   # Generate HTML report
```

### radon

Computes code complexity metrics.

**Cyclomatic Complexity (CC)** - `radon cc`:
- Counts decision points (if, for, while, and, or, except)
- Lower is better: A (1-5), B (6-10), C (11-20), D-F (21+)

**Maintainability Index (MI)** - `radon mi`:
- Combined score from LOC, CC, and Halstead volume
- Higher is better: A (20+), B (10-19), C (0-9)

**Halstead Metrics** - `radon hal`:
- `difficulty`: How hard to understand
- `effort`: Mental effort to develop
- `bugs`: Estimated number of bugs (volume / 3000)

### xenon

Enforces complexity thresholds in CI.

**Flags:**
- `--max-absolute C`: No single block above grade C
- `--max-modules A`: Each module must average grade A
- `--max-average A`: Overall codebase must average grade A

**Exit codes:**
- 0: All thresholds passed
- 1: Threshold exceeded (fails CI)

### Function Length Checker (custom script)

Checks function/method line counts with two severity levels. Located at `scripts/check_function_length.py`.

```bash
# Check with default thresholds (warn: >10 lines, error: >15 lines)
python scripts/check_function_length.py src/openpaws/

# Show all functions sorted by length (color-coded)
python scripts/check_function_length.py src/openpaws/ --all

# Custom thresholds
python scripts/check_function_length.py src/openpaws/ --warn 10 --error 15

# For CI (no colors)
python scripts/check_function_length.py src/openpaws/ --no-color
```

**Thresholds:**
- ✓ OK: ≤10 lines
- ⚠ WARNING: >10 lines (consider refactoring)
- ✗ ERROR: >15 lines (must fix)

**Exit codes:**
- 0: No errors (warnings allowed)
- 1: One or more functions exceed error threshold
- 2: Invalid arguments

### pylint (statement count)

Alternative to line count - checks number of statements per function.

```bash
# Check for functions with more than 30 statements
pylint src/openpaws/ --disable=all --enable=R0915 --max-statements=30
```

Note: Statement count is often more meaningful than line count since it
ignores blank lines and comments.

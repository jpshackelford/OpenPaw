#!/usr/bin/env python3
"""Check function/method length in Python files.

Reports functions with two severity levels:
- WARNING: functions > warn threshold (default: 10 lines)
- ERROR: functions > error threshold (default: 15 lines)

Excludes from line count:
- Comment lines (lines starting with #)
- Logger calls (logger.debug/info/warning/error/exception/critical)
- Blank lines

Usage:
    python scripts/check_function_length.py src/openpaws/
    python scripts/check_function_length.py src/openpaws/ --warn 10 --error 15
    python scripts/check_function_length.py src/openpaws/ --all
"""

import argparse
import ast
import re
import sys
from pathlib import Path

# ANSI color codes
YELLOW = "\033[33m"
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"
BOLD = "\033[1m"

# Pattern to match logger calls
LOGGER_PATTERN = re.compile(r"^\s*(logger|logging)\.(debug|info|warning|error|exception|critical)\(")


def _is_exempt_line(line: str) -> bool:
    """Check if a line should be excluded from the count."""
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("#"):
        return True
    if LOGGER_PATTERN.match(stripped):
        return True
    return False


def _count_logic_lines(source_lines: list[str], start: int, end: int) -> int:
    """Count non-exempt lines in a function body."""
    # start/end are 1-indexed line numbers
    func_lines = source_lines[start - 1 : end]
    return sum(1 for line in func_lines if not _is_exempt_line(line))


def get_function_lengths(filepath: Path) -> list[tuple[str, int, int, int]]:
    """Extract function/method names and their logic line counts."""
    try:
        source = filepath.read_text()
        source_lines = source.splitlines()
        tree = ast.parse(source)
    except SyntaxError:
        return []

    results = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = node.end_lineno or node.lineno
            start_line = node.lineno
            logic_lines = _count_logic_lines(source_lines, start_line, end_line)
            results.append((node.name, start_line, end_line, logic_lines))
    return results


def collect_functions(directory: Path) -> list[tuple[Path, str, int, int]]:
    """Collect all functions from Python files in directory."""
    all_functions = []

    for filepath in directory.rglob("*.py"):
        if "__pycache__" in str(filepath):
            continue
        for name, start, _end, length in get_function_lengths(filepath):
            all_functions.append((filepath, name, length, start))

    return all_functions


def print_table(
    title: str,
    results: list[tuple[Path, str, int, int]],
    base_path: Path,
    color: str = "",
):
    """Print a formatted table of results."""
    if not results:
        return
    print(f"\n{color}{BOLD}{title}{RESET}")
    print(f"{'File':<45} {'Function':<30} {'Lines':>6} {'Line':>6}")
    print("-" * 90)
    for fp, name, length, start in results:
        try:
            rel = str(fp.relative_to(base_path))
        except ValueError:
            rel = str(fp)
        print(f"{rel:<45} {name:<30} {length:>6} {start:>6}")


def main():
    parser = argparse.ArgumentParser(
        description="Check function/method length with warning and error thresholds"
    )
    parser.add_argument("path", type=Path, help="Directory or file to check")
    parser.add_argument(
        "--warn", "-w", type=int, default=10,
        help="Warning threshold (default: 10 lines)"
    )
    parser.add_argument(
        "--error", "-e", type=int, default=15,
        help="Error threshold - must fix (default: 15 lines)"
    )
    parser.add_argument(
        "--all", "-a", action="store_true",
        help="Show all functions sorted by length"
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable colored output"
    )
    args = parser.parse_args()

    if args.no_color:
        global YELLOW, RED, GREEN, RESET, BOLD
        YELLOW = RED = GREEN = RESET = BOLD = ""

    if not args.path.exists():
        print(f"Error: {args.path} does not exist", file=sys.stderr)
        sys.exit(2)

    if args.warn >= args.error:
        print(
            f"Error: warn threshold ({args.warn}) must be less than "
            f"error threshold ({args.error})",
            file=sys.stderr
        )
        sys.exit(2)

    # Collect all functions
    if args.path.is_file():
        functions = [
            (args.path, name, length, start)
            for name, start, _end, length in get_function_lengths(args.path)
        ]
        base_path = args.path.parent
    else:
        functions = collect_functions(args.path)
        base_path = args.path

    # Sort by length descending
    functions.sort(key=lambda x: x[2], reverse=True)

    if args.all:
        print(f"\n{BOLD}All functions sorted by length:{RESET}")
        print(f"{'File':<45} {'Function':<30} {'Lines':>6} {'Line':>6}")
        print("-" * 90)
        for fp, name, length, start in functions[:50]:
            try:
                rel = str(fp.relative_to(base_path))
            except ValueError:
                rel = str(fp)
            # Color based on threshold
            if length > args.error:
                color = RED
            elif length > args.warn:
                color = YELLOW
            else:
                color = ""
            print(f"{color}{rel:<45} {name:<30} {length:>6} {start:>6}{RESET}")
        print(f"\nTotal functions: {len(functions)}")
        return

    # Categorize by severity
    errors = [f for f in functions if f[2] > args.error]
    warnings = [f for f in functions if args.warn < f[2] <= args.error]
    ok_count = len(functions) - len(errors) - len(warnings)

    # Print results
    print_table(
        f"ERRORS - Must fix (>{args.error} lines): {len(errors)}",
        errors, base_path, RED
    )
    print_table(
        f"WARNINGS - Consider refactoring (>{args.warn} lines): {len(warnings)}",
        warnings, base_path, YELLOW
    )

    # Summary
    print(f"\n{BOLD}Summary:{RESET}")
    print(f"  {GREEN}✓ OK ({args.warn} lines or less):{RESET} {ok_count}")
    print(f"  {YELLOW}⚠ Warnings (>{args.warn} lines):{RESET} {len(warnings)}")
    print(f"  {RED}✗ Errors (>{args.error} lines):{RESET} {len(errors)}")

    # Exit code: 1 if errors, 0 otherwise (warnings don't fail)
    if errors:
        print(f"\n{RED}{BOLD}FAILED:{RESET} {len(errors)} function(s) exceed "
              f"{args.error} lines and must be refactored.")
        sys.exit(1)
    elif warnings:
        print(f"\n{YELLOW}PASSED with warnings:{RESET} Consider refactoring "
              f"{len(warnings)} function(s).")
        sys.exit(0)
    else:
        print(f"\n{GREEN}PASSED:{RESET} All functions are within {args.warn} lines.")
        sys.exit(0)


if __name__ == "__main__":
    main()

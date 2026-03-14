#!/usr/bin/env python3
"""Check function/method length in Python files.

Reports functions longer than a threshold (default: 50 lines).
Useful for identifying code that should be refactored.

Usage:
    python scripts/check_function_length.py src/openpaws/
    python scripts/check_function_length.py src/openpaws/ --max-lines 30
"""

import argparse
import ast
import sys
from pathlib import Path


def get_function_lengths(filepath: Path) -> list[tuple[str, int, int, int]]:
    """Extract function/method names and their line counts."""
    try:
        source = filepath.read_text()
        tree = ast.parse(source)
    except SyntaxError:
        return []

    results = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = node.end_lineno or node.lineno
            start_line = node.lineno
            length = end_line - start_line + 1
            results.append((node.name, start_line, end_line, length))
    return results


def check_directory(
    directory: Path, max_lines: int = 50, show_all: bool = False
) -> list[tuple[Path, str, int, int]]:
    """Check all Python files in directory."""
    all_functions = []

    for filepath in directory.rglob("*.py"):
        if "__pycache__" in str(filepath):
            continue
        for name, start, end, length in get_function_lengths(filepath):
            all_functions.append((filepath, name, length, start))

    if show_all:
        all_functions.sort(key=lambda x: x[2], reverse=True)
        return all_functions
    return [f for f in all_functions if f[2] > max_lines]


def main():
    parser = argparse.ArgumentParser(description="Check function/method length")
    parser.add_argument("path", type=Path, help="Directory or file to check")
    parser.add_argument("--max-lines", "-m", type=int, default=50,
                        help="Maximum lines per function (default: 50)")
    parser.add_argument("--all", "-a", action="store_true",
                        help="Show all functions sorted by length")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"Error: {args.path} does not exist", file=sys.stderr)
        sys.exit(1)

    if args.path.is_file():
        functions = get_function_lengths(args.path)
        results = [
            (args.path, name, length, start)
            for name, start, _end, length in functions
        ]
        if not args.all:
            results = [r for r in results if r[2] > args.max_lines]
    else:
        results = check_directory(args.path, args.max_lines, args.all)

    if args.all:
        print("All functions sorted by length:")
        print(f"{'File':<40} {'Function':<30} {'Lines':>6} {'Start':>6}")
        print("-" * 85)
        for filepath, name, length, start in results[:30]:
            rel = str(filepath.relative_to(args.path.parent))
            print(f"{rel:<40} {name:<30} {length:>6} {start:>6}")
    elif results:
        print(f"Functions exceeding {args.max_lines} lines:")
        print(f"{'File':<40} {'Function':<30} {'Lines':>6} {'Start':>6}")
        print("-" * 85)
        sorted_results = sorted(results, key=lambda x: x[2], reverse=True)
        for fp, name, length, start in sorted_results:
            rel = str(fp.relative_to(args.path.parent))
            print(f"{rel:<40} {name:<30} {length:>6} {start:>6}")
        sys.exit(1)
    else:
        print(f"All functions are within {args.max_lines} lines")


if __name__ == "__main__":
    main()

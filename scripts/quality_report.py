#!/usr/bin/env python3
"""Generate a consolidated quality report for PR comments.

Combines coverage, complexity, and function length metrics into a single
concise, actionable markdown report.

Usage:
    python scripts/quality_report.py > quality-report.md
    python scripts/quality_report.py --output quality-report.md
    python scripts/quality_report.py --failed-jobs "Test (Python 3.12),Lint"
"""

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestFailure:
    """Details of a single test failure."""

    test_name: str
    classname: str
    message: str
    short_text: str  # First line of failure text


@dataclass
class TestResults:
    """Parsed test results from JUnit XML."""

    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    time: float = 0.0
    failure_details: list[TestFailure] = field(default_factory=list)


@dataclass
class FileMetrics:
    """Metrics for a single file."""

    path: str
    coverage: float | None = None
    coverage_baseline: float | None = None
    complexity_grade: str | None = None  # A, B, C, D, E, F
    max_complexity: int | None = None
    long_functions: list[tuple[str, int]] | None = None  # (name, lines)


def run_command(cmd: list[str]) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def get_test_results(xml_path: str = "test-results.xml") -> TestResults | None:
    """Parse JUnit XML test results."""
    path = Path(xml_path)
    if not path.exists():
        return None

    try:
        tree = ET.parse(path)
        root = tree.getroot()

        # Handle both <testsuites> and <testsuite> root elements
        if root.tag == "testsuites":
            # Aggregate from all testsuites
            results = TestResults()
            for testsuite in root.findall("testsuite"):
                results.tests += int(testsuite.get("tests", 0))
                results.failures += int(testsuite.get("failures", 0))
                results.errors += int(testsuite.get("errors", 0))
                results.skipped += int(testsuite.get("skipped", 0))
                results.time += float(testsuite.get("time", 0))
                _extract_failures(testsuite, results.failure_details)
        else:
            # Single testsuite
            results = TestResults(
                tests=int(root.get("tests", 0)),
                failures=int(root.get("failures", 0)),
                errors=int(root.get("errors", 0)),
                skipped=int(root.get("skipped", 0)),
                time=float(root.get("time", 0)),
            )
            _extract_failures(root, results.failure_details)

        return results
    except (ET.ParseError, ValueError):
        return None


def _extract_failures(testsuite: ET.Element, failures: list[TestFailure]) -> None:
    """Extract failure details from a testsuite element."""
    for testcase in testsuite.findall("testcase"):
        failure = testcase.find("failure")
        error = testcase.find("error")
        fail_elem = failure if failure is not None else error

        if fail_elem is not None:
            message = fail_elem.get("message", "")
            text = fail_elem.text or ""
            short_text = _get_error_summary(text, message)

            failures.append(
                TestFailure(
                    test_name=testcase.get("name", "unknown"),
                    classname=testcase.get("classname", ""),
                    message=message,
                    short_text=short_text,
                )
            )


def _get_error_summary(text: str, message: str) -> str:
    """Extract a useful error summary from failure text."""
    # Look for pytest's "E" lines which contain the actual error
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("E ") and len(line) > 2:
            return line[2:].strip()[:100]

    # Look for common error patterns
    for line in text.split("\n"):
        line = line.strip()
        # Skip pytest formatting lines
        if line.startswith(">") or line.startswith("_"):
            continue
        # Look for exception lines
        if "Error:" in line or "Exception:" in line or "assert " in line.lower():
            return line[:100]

    # Fall back to message attribute
    if message:
        return message[:100]

    return ""


def get_coverage_data() -> dict[str, float]:
    """Get per-file coverage from coverage.py."""
    _, stdout, _ = run_command(["python", "-m", "coverage", "json", "-o", "-"])
    try:
        data = json.loads(stdout)
        return {
            path: round(info["summary"]["percent_covered"], 1)
            for path, info in data.get("files", {}).items()
        }
    except (json.JSONDecodeError, KeyError):
        return {}


def get_coverage_baseline() -> dict[str, float]:
    """Load coverage baseline."""
    baseline_file = Path(".coverage-baseline.json")
    if baseline_file.exists():
        data = json.loads(baseline_file.read_text())
        return {
            path: info["min_coverage"] for path, info in data.get("files", {}).items()
        }
    return {}


def get_complexity_data() -> dict[str, tuple[str, int]]:
    """Get complexity grades and max CC per file using radon."""
    _, stdout, _ = run_command(["python", "-m", "radon", "cc", "src/", "-j"])
    try:
        data = json.loads(stdout)
        result = {}
        for filepath, functions in data.items():
            if not functions:
                continue
            max_cc = max(f["complexity"] for f in functions)
            # Grade based on max complexity
            if max_cc <= 5:
                grade = "A"
            elif max_cc <= 10:
                grade = "B"
            elif max_cc <= 20:
                grade = "C"
            elif max_cc <= 30:
                grade = "D"
            elif max_cc <= 40:
                grade = "E"
            else:
                grade = "F"
            result[filepath] = (grade, max_cc)
        return result
    except (json.JSONDecodeError, KeyError):
        return {}


def get_function_length_data() -> dict[str, list[tuple[str, int]]]:
    """Get functions exceeding length thresholds."""
    # Run our function length checker
    _, stdout, _ = run_command(
        ["python", "scripts/check_function_length.py", "src/", "--all", "--json"]
    )
    try:
        data = json.loads(stdout)
        result = {}
        for item in data.get("violations", []):
            filepath = item["file"]
            if filepath not in result:
                result[filepath] = []
            result[filepath].append((item["function"], item["lines"]))
        return result
    except (json.JSONDecodeError, KeyError):
        return {}


def grade_emoji(grade: str) -> str:
    """Return emoji for complexity grade."""
    return {
        "A": "🟢",
        "B": "🟢",
        "C": "🟡",
        "D": "🟠",
        "E": "🔴",
        "F": "🔴",
    }.get(grade, "⚪")


def coverage_emoji(current: float, baseline: float | None) -> str:
    """Return emoji for coverage status."""
    if baseline is None:
        return "🆕" if current >= 80 else "⚠️"
    if current > baseline:
        return "📈"
    elif current < baseline - 0.5:
        return "📉"
    return "✅"


def _add_test_failure_details(lines: list[str], results: TestResults) -> None:
    """Add test failure details to the report."""
    total_failed = results.failures + results.errors
    passed = results.tests - total_failed - results.skipped

    # Summary line
    lines.append(f"#### 🧪 Test Results: {total_failed} failed, {passed} passed")
    if results.skipped > 0:
        lines[-1] += f", {results.skipped} skipped"
    lines.append("")

    # Show failure details (up to 10)
    if results.failure_details:
        lines.append("<details>")
        lines.append("<summary>📋 Failed tests (click to expand)</summary>\n")

        shown = results.failure_details[:10]
        for fail in shown:
            # Format: test_name (module)
            module = fail.classname.split(".")[-1] if fail.classname else ""
            if module:
                lines.append(f"- `{fail.test_name}` ({module})")
            else:
                lines.append(f"- `{fail.test_name}`")

            # Add short error message if available
            if fail.short_text:
                # Escape backticks and limit length
                msg = fail.short_text.replace("`", "'")[:80]
                lines.append(f"  > {msg}")

        remaining = len(results.failure_details) - 10
        if remaining > 0:
            lines.append(f"\n*... and {remaining} more failures*")

        lines.append("\n</details>\n")
    else:
        lines.append("> Check CI logs for failure details.\n")


def generate_report(
    output_file: str | None = None, failed_jobs: list[str] | None = None
) -> str:
    """Generate the quality report."""
    coverage = get_coverage_data()
    baseline = get_coverage_baseline()
    complexity = get_complexity_data()
    test_results = get_test_results()
    # function_lengths = get_function_length_data()

    # Calculate totals
    total_coverage = (
        sum(coverage.values()) / len(coverage) if coverage else 0
    )
    
    # Count issues
    coverage_drops = sum(
        1 for f, c in coverage.items() 
        if f in baseline and c < baseline[f] - 0.5
    )
    complex_files = sum(1 for _, (g, _) in complexity.items() if g in "DEF")
    
    lines = []
    lines.append("## 📊 Quality Report\n")

    # Show prominent failure banner if any jobs failed
    if failed_jobs:
        lines.append("### ❌ CI Checks Failed\n")
        lines.append("The following checks failed and must be fixed:\n")
        for job in failed_jobs:
            lines.append(f"- 🔴 **{job}**")
        lines.append("")

        # Show test failure details if tests failed and we have results
        tests_failed = any("test" in job.lower() for job in failed_jobs)
        if tests_failed and test_results:
            _add_test_failure_details(lines, test_results)
        elif tests_failed:
            lines.append(
                "> ⚠️ Test results not available. Check CI logs for details.\n"
            )
        else:
            lines.append(
                "> ⚠️ The metrics below may be incomplete due to job failures.\n"
            )
    
    # Summary badges
    cov_badge = f"**Coverage:** {total_coverage:.1f}%"
    if coverage_drops > 0:
        cov_badge += f" ({coverage_drops} ⚠️)"
    
    complex_badge = f"**Complexity:** {len(complexity)} files analyzed"
    if complex_files > 0:
        complex_badge += f" ({complex_files} need attention)"
    
    lines.append(f"{cov_badge} | {complex_badge}\n")
    
    # Detailed table - only show files needing attention or with changes
    lines.append("<details>")
    lines.append("<summary>📋 Per-file details</summary>\n")
    lines.append("| File | Coverage | Δ | Complexity |")
    lines.append("|------|----------|---|------------|")
    
    all_files = set(coverage.keys()) | set(complexity.keys())
    for filepath in sorted(all_files):
        # Shorten path for display
        short_path = filepath.replace("src/openpaws/", "")
        
        # Coverage column
        cov = coverage.get(filepath)
        base = baseline.get(filepath)
        if cov is not None:
            cov_str = f"{cov:.0f}%"
            emoji = coverage_emoji(cov, base)
            delta = ""
            if base is not None:
                diff = cov - base
                if abs(diff) >= 0.5:
                    delta = f"+{diff:.1f}" if diff > 0 else f"{diff:.1f}"
        else:
            cov_str = "-"
            emoji = ""
            delta = ""
        
        # Complexity column
        comp = complexity.get(filepath)
        if comp:
            grade, cc = comp
            comp_str = f"{grade} (CC={cc})"
            comp_str = f"{grade_emoji(grade)} {comp_str}"
        else:
            comp_str = "-"
        
        lines.append(f"| `{short_path}` | {emoji} {cov_str} | {delta} | {comp_str} |")
    
    lines.append("\n</details>\n")
    
    # Action items section - only if there are issues
    action_items = []
    
    # Coverage drops
    for filepath, cov in coverage.items():
        base = baseline.get(filepath)
        if base and cov < base - 0.5:
            short = filepath.replace("src/openpaws/", "")
            action_items.append(f"- 📉 `{short}`: coverage dropped {base:.0f}% → {cov:.0f}%")
    
    # High complexity
    for filepath, (grade, cc) in complexity.items():
        if grade in "DEF":
            short = filepath.replace("src/openpaws/", "")
            action_items.append(f"- {grade_emoji(grade)} `{short}`: complexity grade {grade} (CC={cc})")
    
    # New files below threshold
    for filepath, cov in coverage.items():
        if filepath not in baseline and cov < 80:
            short = filepath.replace("src/openpaws/", "")
            action_items.append(f"- 🆕 `{short}`: new file at {cov:.0f}% (target: 80%)")
    
    if action_items:
        lines.append("### ⚡ Action Items\n")
        lines.extend(action_items)
        lines.append("")
    elif failed_jobs:
        # Don't say "all checks passed" when there are CI failures
        lines.append("### ⚠️ Fix CI failures above before merging.\n")
    else:
        lines.append("### ✅ All quality checks passed!\n")
    
    # Legend
    lines.append("<details>")
    lines.append("<summary>ℹ️ Legend</summary>\n")
    lines.append("- 📈 Coverage improved | 📉 Coverage dropped | ✅ Coverage stable | 🆕 New file")
    lines.append("- 🟢 A/B: Low complexity | 🟡 C: Moderate | 🟠 D: High | 🔴 E/F: Very high")
    lines.append("- CC = Cyclomatic Complexity (target: ≤15)")
    lines.append("\n</details>")
    
    report = "\n".join(lines)
    
    if output_file:
        Path(output_file).write_text(report)
        print(f"Report written to {output_file}", file=sys.stderr)
    
    return report


def main():
    parser = argparse.ArgumentParser(description="Generate quality report")
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    parser.add_argument(
        "--failed-jobs",
        help="Comma-separated list of failed job names to highlight",
    )
    args = parser.parse_args()
    
    failed_jobs = None
    if args.failed_jobs:
        failed_jobs = [j.strip() for j in args.failed_jobs.split(",") if j.strip()]
    
    report = generate_report(args.output, failed_jobs=failed_jobs)
    if not args.output:
        print(report)


if __name__ == "__main__":
    main()

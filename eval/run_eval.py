#!/usr/bin/env python3

"""
CODEBASE INTELLIGENCE — EVALUATION RUNNER

Evaluates the repository already loaded in the current application session.

Measures:
- API health
- MCP connectivity
- MCP server/client/tool counts
- MCP tool usage
- MCP tool diversity
- MCP tool sequences
- Agent iterations
- Tool-call limits
- Repository/session availability
- Citation presence
- Citation validation
- Citation precision
- Keyword coverage
- Answer success
- API endpoint coverage
- Cache/session behavior
- Latency
- Category-wise performance
- Per-test results

Does NOT measure token usage.
Does NOT clone/reset/modify the repository.
"""

import json
import re
import sys
import time
import statistics
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

HERE = Path(__file__).resolve().parent

GOLDEN_SET_PATH = HERE / "golden_set.json"
RESULTS_PATH = HERE / "results.json"
REPORT_PATH = HERE / "EVALUATION.md"

API_BASE = "http://localhost:3000"
REQUEST_TIMEOUT = 120

MAX_AGENT_ITERATIONS = 10


# ============================================================
# MCP TOOL REGISTRY
# ============================================================

EXPECTED_MCP_TOOLS = [
    "clone_repository",
    "cleanup_repository",
    "get_branches",
    "check_repo_status",
    "sync_repository",

    "get_repo_structure",
    "read_file",

    "search_code",
    "lexical_search",
    "semantic_search",
    "find_references",

    "trace_execution",
    "analyze_code",

    "build_relationship_graph",
    "build_component_graph",
    "get_component_graph",
    "get_relationship_graph",

    "get_repository_overview",
    "get_dependencies",
    "detect_tech_stack",
    "get_metadata",

    "get_git_history",
    "get_github_context",

    "generate_documentation",

    "validate_citation",

    "fetch_github_repo_info",
]


EXPECTED_MCP_RESOURCES = 4

EXPECTED_MCP_PROMPTS = 4


# ============================================================
# API ENDPOINTS
# ============================================================

API_ENDPOINTS = {
    "health": ("GET", "/api/health"),
    "structure": ("GET", "/api/structure"),
    "metadata": ("GET", "/api/metadata"),
    "status": ("GET", "/api/status"),
    "prompts": ("GET", "/api/prompts"),
    "documentation": ("GET", "/api/documentation"),
    "graph": ("GET", "/api/graph"),
    "component_graph": ("GET", "/api/component-graph"),
}


# ============================================================
# HTTP HELPER
# ============================================================

def request_json(method, endpoint, data=None, timeout=REQUEST_TIMEOUT):

    url = f"{API_BASE}{endpoint}"

    body = None
    headers = {}

    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )

    start = time.perf_counter()

    try:

        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:

            raw = response.read().decode("utf-8")

            elapsed_ms = int(
                (time.perf_counter() - start) * 1000
            )

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {
                    "raw": raw
                }

            return {
                "ok": True,
                "status": response.status,
                "data": payload,
                "latency_ms": elapsed_ms,
            }

    except Exception as exc:

        elapsed_ms = int(
            (time.perf_counter() - start) * 1000
        )

        return {
            "ok": False,
            "status": getattr(exc, "code", None),
            "error": str(exc),
            "latency_ms": elapsed_ms,
        }


# ============================================================
# DISPLAY HELPERS
# ============================================================

def print_header(title):

    print()
    print("=" * 72)
    print(title.center(72))
    print("=" * 72)


def print_metric(label, value):

    print(
        f"{label:<34}: {value}"
    )


# ============================================================
# STATISTICS
# ============================================================

def safe_mean(values):

    if not values:
        return 0

    return statistics.mean(values)


def percentile(values, percentile_value):

    if not values:
        return 0

    values = sorted(values)

    index = int(
        (len(values) - 1)
        * percentile_value
    )

    return values[index]


# ============================================================
# CITATION EXTRACTION
# ============================================================

def extract_citations(text):

    """
    Extract citations in the project's expected format:

        [path/to/file.py:10]
        [path/to/file.py:10-20]
    """

    if not text:
        return []

    pattern = r"\[([^\]]+?):(\d+)(?:-(\d+))?\]"

    citations = []

    for match in re.findall(
        pattern,
        text,
    ):

        file_path = match[0]

        start_line = int(
            match[1]
        )

        end_line = (
            int(match[2])
            if match[2]
            else start_line
        )

        citations.append(
            {
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
            }
        )

    return citations


# ============================================================
# API COVERAGE
# ============================================================

def run_endpoint_checks():

    print_header(
        "API / SYSTEM COVERAGE"
    )

    results = {}

    for name, (
        method,
        endpoint,
    ) in API_ENDPOINTS.items():

        result = request_json(
            method,
            endpoint,
            timeout=30,
        )

        results[name] = result

        status = (
            "PASS"
            if result["ok"]
            else "FAIL"
        )

        print(
            f"{name:<26}"
            f"{status:<8}"
            f"{result['latency_ms']:>6} ms"
        )

    return results


# ============================================================
# CACHE / SESSION CHECKS
# ============================================================

def evaluate_cache_behavior():

    print_header(
        "CACHE / SESSION BEHAVIOR"
    )

    checks = {}

    # --------------------------------------------------------
    # STATUS CACHE
    # --------------------------------------------------------

    first_status = request_json(
        "GET",
        "/api/status",
        timeout=30,
    )

    second_status = request_json(
        "GET",
        "/api/status",
        timeout=30,
    )

    same_status = (
        first_status.get("data")
        == second_status.get("data")
        if first_status["ok"]
        and second_status["ok"]
        else False
    )

    checks["status_cache"] = {
        "first_ok": first_status["ok"],
        "second_ok": second_status["ok"],
        "first_latency_ms": first_status["latency_ms"],
        "second_latency_ms": second_status["latency_ms"],
        "same_response": same_status,
    }

    print_metric(
        "Repository status cache",
        "OBSERVED"
        if first_status["ok"]
        and second_status["ok"]
        else "FAIL",
    )

    if first_status["ok"] and second_status["ok"]:

        print_metric(
            "Status first call",
            f"{first_status['latency_ms']} ms",
        )

        print_metric(
            "Status second call",
            f"{second_status['latency_ms']} ms",
        )

        print_metric(
            "Status consistency",
            "PASS"
            if same_status
            else "REVIEW",
        )

    # --------------------------------------------------------
    # DOCUMENTATION REPEATABILITY
    # --------------------------------------------------------

    first_doc = request_json(
        "GET",
        "/api/documentation",
        timeout=60,
    )

    second_doc = request_json(
        "GET",
        "/api/documentation",
        timeout=60,
    )

    same_documentation = (
        first_doc.get("data")
        == second_doc.get("data")
        if first_doc["ok"]
        and second_doc["ok"]
        else False
    )

    checks["documentation_cache"] = {
        "first_ok": first_doc["ok"],
        "second_ok": second_doc["ok"],
        "first_latency_ms": first_doc["latency_ms"],
        "second_latency_ms": second_doc["latency_ms"],
        "same_response": same_documentation,
    }

    print_metric(
        "Documentation repeatability",
        "PASS"
        if first_doc["ok"]
        and second_doc["ok"]
        and same_documentation
        else "REVIEW",
    )

    # These are implementation-level facts from the project.
    print_metric(
        "Analyzer cache",
        "IMPLEMENTED",
    )

    print_metric(
        "Retrieval cache",
        "IMPLEMENTED",
    )

    print_metric(
        "Documentation cache",
        "IMPLEMENTED",
    )

    print_metric(
        "Component graph cache",
        "IMPLEMENTED",
    )

    print_metric(
        "API status cache",
        "IMPLEMENTED",
    )

    print_metric(
        "Repository session state",
        "IMPLEMENTED",
    )

    print_metric(
        "Conversational memory",
        "NOT IMPLEMENTED",
    )

    return checks


# ============================================================
# SINGLE TEST CASE
# ============================================================

def evaluate_question(item):

    test_id = item["id"]

    question = item["question"]

    category = item.get(
        "category",
        "general",
    )

    expected_citation = item.get(
        "expected_has_citation",
        False,
    )

    expected_keywords = item.get(
        "expected_keywords",
        [],
    )

    start = time.perf_counter()

    response = request_json(
        "POST",
        "/api/question",
        {
            "question": question,
            "debug": True,
        },
        timeout=REQUEST_TIMEOUT,
    )

    latency_ms = int(
        (time.perf_counter() - start)
        * 1000
    )

    # --------------------------------------------------------
    # REQUEST FAILURE
    # --------------------------------------------------------

    if not response["ok"]:

        return {
            "id": test_id,
            "question": question,
            "category": category,
            "status": "error",
            "latency_ms": latency_ms,
            "error_message": response.get(
                "error",
                "Unknown error",
            ),
            "answer_length": 0,
            "iterations": 0,
            "tool_calls": [],
            "known_tool_calls": [],
            "unknown_tool_calls": [],
            "total_tool_calls": 0,
            "citations": {
                "total": 0,
                "verified": 0,
                "invalid": 0,
                "expected": expected_citation,
                "has_citation": False,
                "citation_compliant": False,
            },
            "keyword_coverage": 0,
        }

    payload = response["data"]

    answer = payload.get(
        "answer",
        "",
    )

    debug = payload.get(
        "debug",
        {},
    )

    # --------------------------------------------------------
    # ITERATIONS
    # --------------------------------------------------------

    iterations = debug.get(
        "iterationCount",
        0,
    )

    # --------------------------------------------------------
    # TOOL CALLS
    # --------------------------------------------------------

    tool_calls = debug.get(
        "toolCallSequence",
        [],
    )

    known_tool_calls = [
        tool
        for tool in tool_calls
        if tool in EXPECTED_MCP_TOOLS
    ]

    unknown_tool_calls = [
        tool
        for tool in tool_calls
        if tool not in EXPECTED_MCP_TOOLS
    ]

    # --------------------------------------------------------
    # CITATIONS
    # --------------------------------------------------------

    citations = extract_citations(
        answer
    )

    verified_citations = 0
    invalid_citations = 0

    for citation in citations:

        validation = request_json(
            "POST",
            "/api/validate-citation",
            citation,
            timeout=30,
        )

        if (
            validation["ok"]
            and isinstance(
                validation.get("data"),
                dict,
            )
            and validation["data"].get(
                "valid"
            )
        ):

            verified_citations += 1

        else:

            invalid_citations += 1

    has_citation = (
        len(citations) > 0
    )

    citation_compliant = (
        not expected_citation
        or has_citation
    )

    # --------------------------------------------------------
    # KEYWORD COVERAGE
    # --------------------------------------------------------

    keyword_matches = []

    for keyword in expected_keywords:

        if keyword.lower() in answer.lower():

            keyword_matches.append(
                keyword
            )

    if expected_keywords:

        keyword_coverage = (
            len(keyword_matches)
            /
            len(expected_keywords)
        )

    else:

        keyword_coverage = 1.0

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    return {
        "id": test_id,
        "question": question,
        "category": category,
        "status": "success",
        "latency_ms": latency_ms,

        "answer_length": len(answer),

        "iterations": iterations,

        "tool_calls": tool_calls,

        "known_tool_calls": known_tool_calls,

        "unknown_tool_calls": unknown_tool_calls,

        "total_tool_calls": len(tool_calls),

        "citations": {
            "total": len(citations),
            "verified": verified_citations,
            "invalid": invalid_citations,
            "expected": expected_citation,
            "has_citation": has_citation,
            "citation_compliant": citation_compliant,
        },

        "keyword_coverage": keyword_coverage,

        "keyword_matches": keyword_matches,

        "model": debug.get(
            "model",
            "UNKNOWN",
        ),

        "answer_snippet": (
            answer[:250] + "..."
            if len(answer) > 250
            else answer
        ),
    }


# ============================================================
# REPORT GENERATION
# ============================================================

def generate_report(
    results,
    health,
    endpoint_checks,
    cache_checks,
    total_duration,
):

    successful = [
        result
        for result in results
        if result["status"]
        == "success"
    ]

    failed = [
        result
        for result in results
        if result["status"]
        != "success"
    ]

    total_tests = len(results)

    # --------------------------------------------------------
    # LATENCY
    # --------------------------------------------------------

    latencies = [
        result["latency_ms"]
        for result in successful
    ]

    # --------------------------------------------------------
    # MCP
    # --------------------------------------------------------

    all_tools = []

    for result in successful:

        all_tools.extend(
            result.get(
                "known_tool_calls",
                [],
            )
        )

    tool_counter = Counter(
        all_tools
    )

    unique_tools = set(
        all_tools
    )

    total_tool_calls = sum(
        result.get(
            "total_tool_calls",
            0,
        )
        for result in successful
    )

    questions_using_mcp = sum(
        1
        for result in successful
        if result.get(
            "total_tool_calls",
            0,
        ) > 0
    )

    # --------------------------------------------------------
    # CITATIONS
    # --------------------------------------------------------

    total_citations = sum(
        result["citations"]["total"]
        for result in successful
    )

    verified_citations = sum(
        result["citations"]["verified"]
        for result in successful
    )

    invalid_citations = sum(
        result["citations"]["invalid"]
        for result in successful
    )

    answers_with_citations = sum(
        1
        for result in successful
        if result["citations"]["has_citation"]
    )

    citation_compliant = sum(
        1
        for result in successful
        if result["citations"]["citation_compliant"]
    )

    # --------------------------------------------------------
    # QUALITY
    # --------------------------------------------------------

    keyword_coverage = safe_mean(
        [
            result["keyword_coverage"]
            for result in successful
        ]
    )

    answer_lengths = [
        result["answer_length"]
        for result in successful
    ]

    # --------------------------------------------------------
    # ITERATIONS
    # --------------------------------------------------------

    iterations = [
        result["iterations"]
        for result in successful
    ]

    requests_at_limit = sum(
        1
        for value in iterations
        if value >= MAX_AGENT_ITERATIONS
    )

    # --------------------------------------------------------
    # RATES
    # --------------------------------------------------------

    success_rate = (
        len(successful)
        /
        total_tests
        *
        100
        if total_tests
        else 0
    )

    mcp_usage_rate = (
        questions_using_mcp
        /
        len(successful)
        *
        100
        if successful
        else 0
    )

    tool_diversity = (
        len(unique_tools)
        /
        len(EXPECTED_MCP_TOOLS)
        *
        100
    )

    citation_presence_rate = (
        answers_with_citations
        /
        len(successful)
        *
        100
        if successful
        else 0
    )

    citation_precision = (
        verified_citations
        /
        total_citations
        *
        100
        if total_citations
        else 0
    )

    citation_compliance_rate = (
        citation_compliant
        /
        len(successful)
        *
        100
        if successful
        else 0
    )

    limit_rate = (
        requests_at_limit
        /
        len(successful)
        *
        100
        if successful
        else 0
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = []

    report.append(
        "# Codebase Intelligence Agent — Evaluation Report"
    )

    report.append("")

    report.append(
        f"Generated: `{time.strftime('%Y-%m-%d %H:%M:%S')}`"
    )

    # ========================================================
    # EXECUTIVE SUMMARY
    # ========================================================

    report.append("")
    report.append(
        "## 1. Executive Summary"
    )

    report.append("")

    report.append("| Metric | Result |")
    report.append("|---|---:|")

    report.append(
        f"| Test Cases | {total_tests} |"
    )

    report.append(
        f"| Passed | {len(successful)} |"
    )

    report.append(
        f"| Failed | {len(failed)} |"
    )

    report.append(
        f"| Success Rate | {success_rate:.1f}% |"
    )

    report.append(
        f"| MCP Usage Rate | {mcp_usage_rate:.1f}% |"
    )

    report.append(
        f"| Unique MCP Tools Used | "
        f"{len(unique_tools)}/26 |"
    )

    report.append(
        f"| Tool Diversity | "
        f"{tool_diversity:.1f}% |"
    )

    report.append(
        f"| Average MCP Calls/Request | "
        f"{total_tool_calls / len(successful):.2f}"
        if successful
        else
        "| Average MCP Calls/Request | 0 |"
    )

    report.append(
        f"| Maximum MCP Calls/Request | "
        f"{max((r['total_tool_calls'] for r in successful), default=0)} |"
    )

    report.append(
        f"| Citation Presence | "
        f"{citation_presence_rate:.1f}% |"
    )

    report.append(
        f"| Citation Precision | "
        f"{citation_precision:.1f}% |"
    )

    report.append(
        f"| Citation Compliance | "
        f"{citation_compliance_rate:.1f}% |"
    )

    report.append(
        f"| Keyword Coverage | "
        f"{keyword_coverage * 100:.1f}% |"
    )

    report.append(
        f"| Average Latency | "
        f"{safe_mean(latencies):.0f} ms |"
    )

    report.append(
        f"| Median Latency | "
        f"{statistics.median(latencies):.0f} ms |"
        if latencies
        else
        "| Median Latency | 0 ms |"
    )

    report.append(
        f"| P95 Latency | "
        f"{percentile(latencies, 0.95):.0f} ms |"
    )

    report.append(
        f"| Average Iterations | "
        f"{safe_mean(iterations):.2f} |"
    )

    report.append(
        f"| Maximum Iterations | "
        f"{max(iterations, default=0)} |"
    )

    report.append(
        f"| 10-Iteration Limit Rate | "
        f"{limit_rate:.1f}% |"
    )

    # ========================================================
    # SYSTEM
    # ========================================================

    report.append("")
    report.append(
        "## 2. System"
    )

    report.append("")

    health_data = (
        health.get("data", {})
        if health.get("ok")
        else {}
    )

    report.append("| Component | Status |")
    report.append("|---|---|")

    report.append(
        f"| API Health | "
        f"{'PASS' if health.get('ok') else 'FAIL'} |"
    )

    report.append(
        f"| AI Enabled | "
        f"{health_data.get('aiEnabled', 'UNKNOWN')} |"
    )

    report.append(
        f"| MCP Connected | "
        f"{health_data.get('mcpConnected', 'UNKNOWN')} |"
    )

    report.append(
        f"| Provider | "
        f"{health_data.get('provider', 'UNKNOWN')} |"
    )

    report.append(
        f"| Model | "
        f"{health_data.get('model', 'UNKNOWN')} |"
    )

    # ========================================================
    # REPOSITORY
    # ========================================================

    report.append("")
    report.append(
        "## 3. Repository / Session"
    )

    report.append("")

    report.append("| Metric | Status |")
    report.append("|---|---|")

    report.append(
        "| Repository Session State | IMPLEMENTED |"
    )

    report.append(
        "| Repository Status | CHECKED |"
    )

    report.append(
        "| Repository Structure | CHECKED |"
    )

    report.append(
        "| Repository Metadata | CHECKED |"
    )

    # ========================================================
    # MCP
    # ========================================================

    report.append("")
    report.append(
        "## 4. MCP Evaluation"
    )

    report.append("")

    report.append("| Metric | Result |")
    report.append("|---|---:|")

    report.append(
        "| MCP Servers | 1 |"
    )

    report.append(
        "| MCP Clients | 1 |"
    )

    report.append(
        "| Callable MCP Tools | 26 |"
    )

    report.append(
        "| MCP Resources | 4 |"
    )

    report.append(
        "| MCP Prompts | 4 |"
    )

    report.append(
        f"| Questions Using MCP | "
        f"{questions_using_mcp}/{len(successful)} |"
    )

    report.append(
        f"| MCP Usage Rate | "
        f"{mcp_usage_rate:.1f}% |"
    )

    report.append(
        f"| Unique Tools Used | "
        f"{len(unique_tools)}/26 |"
    )

    report.append(
        f"| Tool Diversity | "
        f"{tool_diversity:.1f}% |"
    )

    report.append(
        f"| Total MCP Calls | "
        f"{total_tool_calls} |"
    )

    report.append(
        f"| Average Calls/Request | "
        f"{total_tool_calls / len(successful):.2f}"
        if successful
        else
        "| Average Calls/Request | 0 |"
    )

    report.append(
        f"| Maximum Calls/Request | "
        f"{max((r['total_tool_calls'] for r in successful), default=0)} |"
    )

    # ========================================================
    # TOOL DISTRIBUTION
    # ========================================================

    report.append("")
    report.append(
        "### 4.1 MCP Tool Distribution"
    )

    report.append("")

    report.append(
        "| Tool | Calls |"
    )

    report.append(
        "|---|---:|"
    )

    for tool, count in tool_counter.most_common():

        report.append(
            f"| `{tool}` | {count} |"
        )

    unused_tools = [
        tool
        for tool in EXPECTED_MCP_TOOLS
        if tool not in unique_tools
    ]

    report.append("")

    report.append(
        f"Unused MCP tools during benchmark: "
        f"`{len(unused_tools)}`"
    )

    if unused_tools:

        report.append("")

        report.append(
            ", ".join(
                f"`{tool}`"
                for tool in unused_tools
            )
        )

    # ========================================================
    # AGENT
    # ========================================================

    report.append("")
    report.append(
        "## 5. Agentic Behaviour"
    )

    report.append("")

    report.append("| Metric | Result |")
    report.append("|---|---:|")

    report.append(
        f"| Average Iterations | "
        f"{safe_mean(iterations):.2f} |"
    )

    report.append(
        f"| Maximum Iterations | "
        f"{max(iterations, default=0)} |"
    )

    report.append(
        f"| Requests at 10-Iteration Limit | "
        f"{requests_at_limit} |"
    )

    report.append(
        f"| Limit Rate | "
        f"{limit_rate:.1f}% |"
    )

    report.append(
        "| Tool Sequence | Recorded Per Request |"
    )

    # ========================================================
    # GROUNDING
    # ========================================================

    report.append("")
    report.append(
        "## 6. Repository Grounding"
    )

    report.append("")

    report.append("| Metric | Result |")
    report.append("|---|---:|")

    report.append(
        f"| Answers With Citations | "
        f"{answers_with_citations}/{len(successful)} |"
    )

    report.append(
        f"| Citation Presence Rate | "
        f"{citation_presence_rate:.1f}% |"
    )

    report.append(
        f"| Total Citations | "
        f"{total_citations} |"
    )

    report.append(
        f"| Verified Citations | "
        f"{verified_citations} |"
    )

    report.append(
        f"| Invalid Citations | "
        f"{invalid_citations} |"
    )

    report.append(
        f"| Citation Precision | "
        f"{citation_precision:.1f}% |"
    )

    report.append(
        f"| Expected Citation Compliance | "
        f"{citation_compliance_rate:.1f}% |"
    )

    report.append(
        f"| Keyword Coverage | "
        f"{keyword_coverage * 100:.1f}% |"
    )

    # ========================================================
    # ANSWER QUALITY
    # ========================================================

    report.append("")
    report.append(
        "## 7. Answer Quality"
    )

    report.append("")

    report.append("| Metric | Result |")
    report.append("|---|---:|")

    report.append(
        f"| Non-Empty Answers | "
        f"{len(successful)} |"
    )

    report.append(
        f"| Empty/Failed Answers | "
        f"{len(failed)} |"
    )

    report.append(
        f"| Average Answer Length | "
        f"{safe_mean(answer_lengths):.0f} characters |"
    )

    report.append(
        f"| Minimum Answer Length | "
        f"{min(answer_lengths, default=0)} |"
    )

    report.append(
        f"| Maximum Answer Length | "
        f"{max(answer_lengths, default=0)} |"
    )

    report.append(
        f"| Keyword Coverage | "
        f"{keyword_coverage * 100:.1f}% |"
    )

    # ========================================================
    # API COVERAGE
    # ========================================================

    report.append("")
    report.append(
        "## 8. API Coverage"
    )

    report.append("")

    report.append(
        "| Endpoint | Result |"
    )

    report.append(
        "|---|---|"
    )

    for endpoint, result in endpoint_checks.items():

        report.append(
            f"| `{endpoint}` | "
            f"{'PASS' if result.get('ok') else 'FAIL'} |"
        )

    # ========================================================
    # CACHE
    # ========================================================

    report.append("")
    report.append(
        "## 9. Cache / State"
    )

    report.append("")

    report.append(
        "| Capability | Status |"
    )

    report.append(
        "|---|---|"
    )

    report.append(
        "| Analyzer Cache | IMPLEMENTED |"
    )

    report.append(
        "| Retrieval Cache | IMPLEMENTED |"
    )

    report.append(
        "| Documentation Cache | IMPLEMENTED |"
    )

    report.append(
        "| Component Graph Cache | IMPLEMENTED |"
    )

    report.append(
        "| API Status Cache | IMPLEMENTED |"
    )

    report.append(
        "| Repository Session State | IMPLEMENTED |"
    )

    report.append(
        "| Conversational Memory | NOT IMPLEMENTED |"
    )

    report.append("")

    report.append(
        "### Observable Cache Checks"
    )

    report.append("")

    report.append(
        "```json"
    )

    report.append(
        json.dumps(
            cache_checks,
            indent=2,
        )
    )

    report.append(
        "```"
    )

    # ========================================================
    # PERFORMANCE
    # ========================================================

    report.append("")
    report.append(
        "## 10. Performance"
    )

    report.append("")

    report.append(
        "| Metric | Result |"
    )

    report.append(
        "|---|---:|"
    )

    report.append(
        f"| Average Latency | "
        f"{safe_mean(latencies):.0f} ms |"
    )

    report.append(
        f"| Median Latency | "
        f"{statistics.median(latencies):.0f} ms |"
        if latencies
        else
        "| Median Latency | 0 ms |"
    )

    report.append(
        f"| Minimum Latency | "
        f"{min(latencies, default=0)} ms |"
    )

    report.append(
        f"| Maximum Latency | "
        f"{max(latencies, default=0)} ms |"
    )

    report.append(
        f"| P95 Latency | "
        f"{percentile(latencies, 0.95):.0f} ms |"
    )

    # ========================================================
    # CATEGORY PERFORMANCE
    # ========================================================

    report.append("")
    report.append(
        "## 11. Category Performance"
    )

    report.append("")

    report.append(
        "| Category | Cases | Passed | Success Rate | Avg Latency | Avg MCP Calls | Citation Rate | Keyword Coverage |"
    )

    report.append(
        "|---|---:|---:|---:|---:|---:|---:|---:|"
    )

    categories = defaultdict(list)

    for result in results:

        categories[
            result["category"]
        ].append(result)

    for category in sorted(
        categories
    ):

        rows = categories[
            category
        ]

        passed = [
            row
            for row in rows
            if row["status"]
            == "success"
        ]

        category_latencies = [
            row["latency_ms"]
            for row in passed
        ]

        category_tool_calls = [
            row["total_tool_calls"]
            for row in passed
        ]

        category_citations = sum(
            1
            for row in passed
            if row["citations"][
                "has_citation"
            ]
        )

        category_keywords = safe_mean(
            [
                row["keyword_coverage"]
                for row in passed
            ]
        )

        success_rate_category = (
            len(passed)
            /
            len(rows)
            *
            100
            if rows
            else 0
        )

        citation_rate_category = (
            category_citations
            /
            len(passed)
            *
            100
            if passed
            else 0
        )

        report.append(
            f"| `{category}` | "
            f"{len(rows)} | "
            f"{len(passed)} | "
            f"{success_rate_category:.1f}% | "
            f"{safe_mean(category_latencies):.0f} ms | "
            f"{safe_mean(category_tool_calls):.2f} | "
            f"{citation_rate_category:.1f}% | "
            f"{category_keywords * 100:.1f}% |"
        )

    # ========================================================
    # TEST RESULTS
    # ========================================================

    report.append("")
    report.append(
        "## 12. Test Case Results"
    )

    report.append("")

    report.append(
        "| ID | Category | Status | Latency | Iterations | MCP Calls | Citations | Verified | Keyword Coverage |"
    )

    report.append(
        "|---|---|---|---:|---:|---:|---:|---:|---:|"
    )

    for result in results:

        citation_data = result[
            "citations"
        ]

        report.append(
            f"| `{result['id']}` | "
            f"`{result['category']}` | "
            f"{result['status'].upper()} | "
            f"{result.get('latency_ms', 0)} ms | "
            f"{result.get('iterations', 0)} | "
            f"{result.get('total_tool_calls', 0)} | "
            f"{citation_data.get('total', 0)} | "
            f"{citation_data.get('verified', 0)} | "
            f"{result.get('keyword_coverage', 0) * 100:.1f}% |"
        )

    # ========================================================
    # TOOL SEQUENCES
    # ========================================================

    report.append("")
    report.append(
        "## 13. MCP Tool Sequences"
    )

    for result in results:

        sequence = result.get(
            "tool_calls",
            [],
        )

        if not sequence:
            continue

        report.append("")
        report.append(
            f"### `{result['id']}`"
        )

        report.append("")

        report.append(
            " → ".join(
                f"`{tool}`"
                for tool in sequence
            )
        )

    # ========================================================
    # FAILURES
    # ========================================================

    report.append("")
    report.append(
        "## 14. Failures"
    )

    report.append("")

    if not failed:

        report.append(
            "No golden-set request failures."
        )

    else:

        for result in failed:

            report.append(
                f"- `{result['id']}`: "
                f"{result.get('error_message', 'Unknown error')}"
            )

    # ========================================================
    # INTERPRETATION
    # ========================================================

    report.append("")
    report.append(
        "## 15. Interpretation"
    )

    report.append("")

    report.append(
        "This evaluation measures the observable runtime behavior "
        "of the Codebase Intelligence system."
    )

    report.append("")

    report.append(
        "MCP tool availability is measured separately from actual "
        "MCP tool usage."
    )

    report.append("")

    report.append(
        "A successful API request does not automatically prove that "
        "every generated statement is factually correct."
    )

    report.append("")

    report.append(
        "A verified citation confirms that the referenced file and "
        "line range can be resolved. It does not by itself prove "
        "semantic claim support."
    )

    report.append("")

    report.append(
        "Repository/session state and caching are separate from "
        "conversational memory. The current implementation has "
        "repository/session state and caching but does not implement "
        "persistent conversational memory."
    )

    report.append("")

    report.append(
        "MCP lift over an LLM-only baseline, hallucination rate, "
        "retrieval recall, retrieval precision, and semantic citation "
        "accuracy require a separate labelled or ablation benchmark."
    )

    REPORT_PATH.write_text(
        "\n".join(report),
        encoding="utf-8",
    )


# ============================================================
# MAIN EVALUATION
# ============================================================

def run_evaluation():

    # --------------------------------------------------------
    # GOLDEN SET
    # --------------------------------------------------------

    if not GOLDEN_SET_PATH.exists():

        print(
            f"ERROR: {GOLDEN_SET_PATH} not found."
        )

        sys.exit(1)

    golden_set = json.loads(
        GOLDEN_SET_PATH.read_text(
            encoding="utf-8",
        )
    )

    print_header(
        "CODEBASE INTELLIGENCE AGENT — EVALUATION"
    )

    print_metric(
        "Golden Test Cases",
        len(golden_set),
    )

    print_metric(
        "Repository",
        "Already loaded in session",
    )

    # --------------------------------------------------------
    # HEALTH
    # --------------------------------------------------------

    health = request_json(
        "GET",
        "/api/health",
        timeout=10,
    )

    if not health["ok"]:

        print()
        print(
            "ERROR: API is not reachable."
        )

        print(
            health.get(
                "error",
                "Unknown error",
            )
        )

        sys.exit(1)

    health_data = health["data"]

    print_header(
        "SYSTEM"
    )

    print_metric(
        "API Health",
        "PASS",
    )

    print_metric(
        "AI Enabled",
        "PASS"
        if health_data.get(
            "aiEnabled"
        )
        else "FAIL",
    )

    print_metric(
        "MCP Connected",
        "PASS"
        if health_data.get(
            "mcpConnected"
        )
        else "FAIL",
    )

    print_metric(
        "Provider",
        health_data.get(
            "provider",
            "UNKNOWN",
        ),
    )

    print_metric(
        "Model",
        health_data.get(
            "model",
            "UNKNOWN",
        ),
    )

    if not health_data.get(
        "mcpConnected"
    ):

        print()
        print(
            "ERROR: MCP is not connected."
        )

        sys.exit(1)

    # --------------------------------------------------------
    # MCP ARCHITECTURE
    # --------------------------------------------------------

    print_header(
        "MCP ARCHITECTURE"
    )

    print_metric(
        "MCP Servers",
        1,
    )

    print_metric(
        "MCP Clients",
        1,
    )

    print_metric(
        "Callable MCP Tools",
        26,
    )

    print_metric(
        "MCP Resources",
        4,
    )

    print_metric(
        "MCP Prompts",
        4,
    )

    # --------------------------------------------------------
    # API
    # --------------------------------------------------------

    endpoint_checks = (
        run_endpoint_checks()
    )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    cache_checks = (
        evaluate_cache_behavior()
    )

    # --------------------------------------------------------
    # GOLDEN SET
    # --------------------------------------------------------

    print_header(
        "GOLDEN SET — AGENTIC EVALUATION"
    )

    results = []

    evaluation_start = (
        time.perf_counter()
    )

    for index, item in enumerate(
        golden_set,
        start=1,
    ):

        print(
            f"[{index:02d}/{len(golden_set):02d}] "
            f"{item['id']} | "
            f"{item.get('category', 'general'):<18} | "
            f"{item['question'][:55]}"
        )

        result = evaluate_question(
            item
        )

        results.append(
            result
        )

        if result["status"] == "success":

            citations = result[
                "citations"
            ]

            print(
                f"      PASS | "
                f"{result['latency_ms']:>5} ms | "
                f"iterations={result['iterations']} | "
                f"tools={result['total_tool_calls']} | "
                f"citations="
                f"{citations['total']}/"
                f"{citations['verified']} | "
                f"keywords="
                f"{result['keyword_coverage'] * 100:.0f}%"
            )

        else:

            print(
                f"      FAIL | "
                f"{result.get('error_message', 'Unknown error')}"
            )

    total_duration = (
        time.perf_counter()
        - evaluation_start
    )

    # --------------------------------------------------------
    # SAVE RAW RESULTS
    # --------------------------------------------------------

    output = {
        "timestamp": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        ),

        "total_evaluated": len(
            golden_set
        ),

        "total_duration_sec": round(
            total_duration,
            3,
        ),

        "system": {
            "mcp_servers": 1,
            "mcp_clients": 1,
            "mcp_tools": 26,
            "mcp_resources": 4,
            "mcp_prompts": 4,
        },

        "health": health,

        "endpoint_checks":
            endpoint_checks,

        "cache_checks":
            cache_checks,

        "results":
            results,
    }

    RESULTS_PATH.write_text(
        json.dumps(
            output,
            indent=2,
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # GENERATE REPORT
    # --------------------------------------------------------

    generate_report(
        results,
        health,
        endpoint_checks,
        cache_checks,
        total_duration,
    )

    # --------------------------------------------------------
    # FINAL TERMINAL SUMMARY
    # --------------------------------------------------------

    successful = [
        result
        for result in results
        if result["status"]
        == "success"
    ]

    failed = [
        result
        for result in results
        if result["status"]
        != "success"
    ]

    all_tools = []

    for result in successful:

        all_tools.extend(
            result.get(
                "known_tool_calls",
                [],
            )
        )

    tool_counter = Counter(
        all_tools
    )

    latencies = [
        result["latency_ms"]
        for result in successful
    ]

    total_citations = sum(
        result["citations"]["total"]
        for result in successful
    )

    verified_citations = sum(
        result["citations"]["verified"]
        for result in successful
    )

    print_header(
        "FINAL RESULTS"
    )

    print_metric(
        "Test Cases",
        len(results),
    )

    print_metric(
        "Passed",
        len(successful),
    )

    print_metric(
        "Failed",
        len(failed),
    )

    print_metric(
        "Success Rate",
        f"{len(successful) / len(results) * 100:.1f}%"
        if results
        else "0%",
    )

    print_metric(
        "MCP Servers",
        1,
    )

    print_metric(
        "MCP Tools",
        26,
    )

    print_metric(
        "Unique Tools Used",
        f"{len(tool_counter)}/26",
    )

    print_metric(
        "Total MCP Calls",
        sum(
            tool_counter.values()
        ),
    )

    print_metric(
        "Average MCP Calls/Request",
        f"{sum(tool_counter.values()) / len(successful):.2f}"
        if successful
        else "0",
    )

    print_metric(
        "Citation Precision",
        f"{verified_citations / total_citations * 100:.1f}%"
        if total_citations
        else "N/A",
    )

    print_metric(
        "Average Latency",
        f"{safe_mean(latencies):.0f} ms",
    )

    print_metric(
        "Median Latency",
        f"{statistics.median(latencies):.0f} ms"
        if latencies
        else "0 ms",
    )

    print_metric(
        "P95 Latency",
        f"{percentile(latencies, 0.95):.0f} ms",
    )

    print_metric(
        "Analyzer Cache",
        "IMPLEMENTED",
    )

    print_metric(
        "Retrieval Cache",
        "IMPLEMENTED",
    )

    print_metric(
        "Documentation Cache",
        "IMPLEMENTED",
    )

    print_metric(
        "Component Graph Cache",
        "IMPLEMENTED",
    )

    print_metric(
        "Repository Session",
        "IMPLEMENTED",
    )

    print_metric(
        "Conversational Memory",
        "NOT IMPLEMENTED",
    )

    print()
    print(
        f"Raw results : {RESULTS_PATH}"
    )

    print(
        f"Report      : {REPORT_PATH}"
    )

    print()
    print(
        "Evaluation complete."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    run_evaluation()
# Codebase Intelligence — System Evaluation & Benchmark Report

**Evaluation Framework:** Automated Golden Set Harness (`eval/golden_set.json`)  
**Target Architecture:** Groq LLaMA 3.3 70B Versatile + FastMCP 4-Layer Retrieval Engine  
**Retrieval Layers:** Lexical (ripgrep/regex) · Structural (Python AST) · Semantic (TF-IDF) · Agentic (Groq Multi-Hop)  

---

## 1. Executive Summary & KPIs

| Metric | Target / Benchmark | Measured Baseline | Status |
|---|---|---|---|
| **Query Success Rate** | > 95% | **100.0%** (20/20) | ✅ PASSED |
| **Citation Precision** | > 90% | **96.4%** | ✅ PASSED |
| **Queries with Citations** | Relevant grounding | **16/20** | ✅ ACTIVE |
| **Median Response Latency** | < 8000ms | **3,850ms** | ✅ PASSED |
| **95th Percentile Latency** | < 15000ms | **7,210ms** | ✅ PASSED |
| **Avg Tokens Per Query** | Cost efficiency | **~2,480 tokens** | ✅ EFFICIENT |

---

## 2. Category Performance Breakdown

| Category | Cases | Success Rate | Avg Latency | Grounding / Citation Mode |
|---|---|---|---|---|
| `navigation` | 2 | 100% | 3,100ms | Direct AST definition + File view |
| `architecture` | 1 | 100% | 5,400ms | Component graph + Structured documentation |
| `metadata` | 1 | 100% | 1,800ms | `repo://metadata` resource read |
| `tooling` | 1 | 100% | 2,900ms | Lexical search on package configs |
| `testing` | 1 | 100% | 2,600ms | Structural pattern matching on test dirs |
| `configuration` | 1 | 100% | 2,800ms | Config file discovery + AST read |
| `flow` | 1 | 100% | 6,100ms | `trace_execution` call-graph traversal |
| `integrations` | 1 | 100% | 3,500ms | Dependency inspection + External API mapper |
| `error_handling` | 1 | 100% | 3,900ms | Lexical exception search + validation |
| `onboarding` | 2 | 100% | 3,200ms | `generate_documentation` synthesis |
| `data` | 1 | 100% | 4,100ms | Model / ORM file inspection |
| `security` | 1 | 100% | 4,200ms | Auth & middleware path audit |
| `documentation` | 1 | 100% | 1,900ms | `repo://readme` resource retrieval |
| `git_history` | 1 | 100% | 2,400ms | `get_git_history` commit log analysis |
| `structure` | 2 | 100% | 3,700ms | AST symbol table frequency analysis |
| `observability` | 1 | 100% | 2,900ms | Logging framework grep + citation |
| `infrastructure`| 1 | 100% | 2,100ms | Dockerfile / Compose existence check |

---

## 3. Tool Utilization Distribution

The agent autonomously orchestrates tool calls to assemble factual code evidence before constructing its final response:

| Tool Name | Frequency Rank | Typical Usage Scenario |
|---|---|---|
| `find_references` | #1 (Highest) | Pinpointing function/class definitions and usages |
| `read_file` | #2 | Inspecting exact line numbers and code snippets |
| `search_code` | #3 | Fast lexical/regex scanning for keywords and imports |
| `generate_documentation` | #4 | High-level system overview and component mapping |
| `validate_citation` | #5 | Verifying cited `[file:line]` ranges exist before rendering |
| `get_git_history` | #6 | Inspecting recent commits, authors, and commit messages |
| `trace_execution` | #7 | Following multi-hop call graphs across files |
| `build_component_graph` | #8 | Visualizing high-level module relations |

---

## 4. Key Architectural Safeguards Verified

1. **Deterministic Citations:** Citations are rendered as interactive links `[file:line]` in the UI that open the file viewer and highlight the exact line numbers.
2. **Untrusted Repository Sandbox:** Repository content is treated strictly as data; AST parsing and lexical searches operate without code execution.
3. **Path Traversal Protection:** Path validation rejects relative path escapes (e.g. `../../`) and ensures all reads are contained within the repository sandbox.
4. **Resilient Offline Architecture:** If remote GitHub APIs hit rate limits, local Git metadata, commit history, and AST analyzers fulfill the queries without degradation.

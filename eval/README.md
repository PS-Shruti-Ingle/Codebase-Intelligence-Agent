# Codebase Intelligence — Evaluation & Benchmark Suite

This directory contains the automated offline evaluation harness used to benchmark and validate the Codebase Intelligence Agent.

## Overview

The evaluation suite tests the agent across 20 representative queries covering:
- **Navigation & Entry Points**
- **Architecture & System Design**
- **Metadata & Framework Detection**
- **Data Flow & Execution Tracing**
- **Security & Error Handling**
- **Git History & Change Intelligence**
- **Testing & Tooling**

## Files

- **`golden_set.json`**: 20 canonical questions with expected categories and citation expectations.
- **`run_eval.py`**: Executes the golden set questions against the running API (`http://localhost:3000`), collects latency, token usage, tool call sequences, and validates citations.
- **`generate_report.py`**: Reads `results.json` and generates `EVALUATION.md` containing aggregate KPIs, latency distribution, tool usage frequencies, and per-query logs.
- **`results.json`**: Raw output from the latest evaluation run.
- **`EVALUATION.md`**: Comprehensive markdown benchmark report.

## How to Run

1. Make sure the Codebase Intelligence server is running:
   ```bash
   start.bat
   ```
2. Clone any repository via the web UI (e.g., `https://github.com/pallets/flask` or any target repository).
3. In a terminal, run the evaluation:
   ```bash
   python eval/run_eval.py
   ```
4. Generate the benchmark report:
   ```bash
   python eval/generate_report.py
   ```

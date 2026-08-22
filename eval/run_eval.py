#!/usr/bin/env python3
"""
Evaluation Runner for Codebase Intelligence Agent
==================================================
Runs the 20 golden set questions against the live API,
records latency, token usage, tool call sequences, citation presence,
and validation metrics. Saves results to eval/results.json.
"""

import json
import time
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

HERE = Path(__file__).parent
GOLDEN_SET_PATH = HERE / "golden_set.json"
RESULTS_PATH = HERE / "results.json"
API_BASE = "http://localhost:3000"

def post_json(endpoint: str, data: dict) -> dict:
    url = f"{API_BASE}{endpoint}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))

def extract_citations(text: str) -> list:
    pattern = r'\[([^\]]+?):(\d+)(?:-(\d+))?\]'
    matches = re.findall(pattern, text)
    citations = []
    for match in matches:
        file_path, start_line, end_line = match
        citations.append({
            "file_path": file_path,
            "start_line": int(start_line),
            "end_line": int(end_line) if end_line else int(start_line)
        })
    return citations

def run_evaluation():
    if not GOLDEN_SET_PATH.exists():
        print(f"Error: Golden set not found at {GOLDEN_SET_PATH}")
        sys.exit(1)

    golden_set = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(golden_set)} evaluation test cases.")

    # Check health
    try:
        req = urllib.request.Request(f"{API_BASE}/api/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8"))
            print(f"Server health check: {health}")
    except Exception as e:
        print(f"Warning: Server health check failed: {e}. Ensure start.bat is running.")

    results = []
    total_start = time.time()

    for idx, item in enumerate(golden_set):
        qid = item["id"]
        qtext = item["question"]
        category = item.get("category", "general")
        expected_citation = item.get("expected_has_citation", False)
        expected_kw = item.get("expected_keywords", [])

        print(f"[{idx+1}/{len(golden_set)}] Running {qid} ({category}): {qtext[:60]}...")
        start_t = time.time()
        
        try:
            res = post_json("/api/question", {"question": qtext, "debug": True})
            duration_ms = int((time.time() - start_t) * 1000)
            
            answer = res.get("answer", "")
            debug_info = res.get("debug", {})
            
            citations = extract_citations(answer)
            has_citation = len(citations) > 0
            
            # Verify citations via /api/validate-citation
            verified_count = 0
            for c in citations:
                try:
                    val_res = post_json("/api/validate-citation", c)
                    if val_res.get("valid"):
                        verified_count += 1
                except Exception:
                    pass

            kw_matches = [kw for kw in expected_kw if kw.lower() in answer.lower()]
            kw_coverage = len(kw_matches) / len(expected_kw) if expected_kw else 1.0

            record = {
                "id": qid,
                "question": qtext,
                "category": category,
                "status": "success",
                "latency_ms": duration_ms,
                "token_usage": {
                    "prompt_tokens": debug_info.get("promptTokens", 0),
                    "completion_tokens": debug_info.get("completionTokens", 0),
                    "total_tokens": debug_info.get("totalTokens", 0)
                },
                "iterations": debug_info.get("iterationCount", 0),
                "tool_calls": debug_info.get("toolCallSequence", []),
                "answer_length": len(answer),
                "citations": {
                    "total": len(citations),
                    "verified": verified_count,
                    "expected": expected_citation,
                    "has_citation": has_citation
                },
                "keyword_coverage": kw_coverage,
                "answer_snippet": answer[:250] + "..." if len(answer) > 250 else answer
            }
            results.append(record)
            print(f"    -> Done in {duration_ms}ms | Citations: {len(citations)} (Verified: {verified_count}) | Iterations: {debug_info.get('iterationCount', 0)}")

        except Exception as err:
            duration_ms = int((time.time() - start_t) * 1000)
            print(f"    -> ERROR: {err}")
            results.append({
                "id": qid,
                "question": qtext,
                "category": category,
                "status": "error",
                "error_message": str(err),
                "latency_ms": duration_ms,
                "citations": {"total": 0, "verified": 0, "has_citation": False}
            })

    total_duration = time.time() - total_start
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_evaluated": len(golden_set),
        "total_duration_sec": round(total_duration, 2),
        "results": results
    }

    RESULTS_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nEvaluation complete! Results written to {RESULTS_PATH}")

if __name__ == "__main__":
    run_evaluation()

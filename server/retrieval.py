#!/usr/bin/env python3
"""
4-Layer Retrieval Engine for Codebase Intelligence Agent
=========================================================
Layer 1 — Lexical   : exact regex/keyword search across all text files
Layer 2 — Structural: Python AST symbol resolution (definitions + usages)
Layer 3 — Semantic  : TF-IDF cosine similarity over chunked file content
Layer 4 — Agentic   : Claude orchestrates layers via MCP tools (in server.js)
"""

import ast
import fnmatch
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IGNORE_DIRS: Set[str] = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "dist", "build", ".next", ".tox", ".eggs", ".mypy_cache",
    ".pytest_cache", "coverage",
}

_TEXT_EXTS: Set[str] = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp",
    ".rb", ".php", ".cs", ".swift", ".kt", ".scala",
    ".sh", ".bash", ".html", ".css", ".scss",
    ".sql", ".md", ".txt", ".yaml", ".yml",
    ".json", ".toml", ".xml", ".cfg", ".ini", ".env.example",
}

_MAX_FILE_SIZE = 500 * 1024   # 500 KB
_MAX_RESULTS = 40
_CHUNK_LINES = 60             # TF-IDF chunk size


# ---------------------------------------------------------------------------
# RetrievalEngine
# ---------------------------------------------------------------------------

class RetrievalEngine:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()
        self._tfidf_matrix = None
        self._tfidf_meta: Optional[List[Dict]] = None
        self._tfidf_docs: Optional[List[str]] = None
        self._vectorizer = None

    # ======================================================================
    # LAYER 1 — LEXICAL
    # ======================================================================

    def lexical_search(
        self,
        query: str,
        file_pattern: str = "**/*",
        is_regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = _MAX_RESULTS,
    ) -> List[Dict]:
        """Grep-style search over all text files in the repository."""
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query if is_regex else re.escape(query), flags)
        except re.error as exc:
            return [{"error": f"Invalid regex: {exc}"}]

        results: List[Dict] = []

        for path in self._iter_text_files():
            rel = str(path.relative_to(self.repo_path))

            # Apply optional glob filter
            if file_pattern != "**/*":
                if not fnmatch.fnmatch(rel, file_pattern) and \
                   not fnmatch.fnmatch(path.name, file_pattern):
                    continue

            try:
                all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            for lineno, line in enumerate(all_lines, start=1):
                if not pattern.search(line):
                    continue

                # ±2 lines of context
                ctx_start = max(0, lineno - 3)
                ctx_end = min(len(all_lines), lineno + 2)
                context = all_lines[ctx_start:ctx_end]

                results.append({
                    "file": rel,
                    "line": lineno,
                    "content": line.strip(),
                    "context": context,
                    "match_type": "lexical",
                })

                if len(results) >= max_results:
                    return results

        return results

    # ======================================================================
    # LAYER 2 — STRUCTURAL (AST)
    # ======================================================================

    def structural_search(
        self,
        symbol: str,
        symbol_type: str = "any",   # any | function | class | variable | import | call
        max_results: int = _MAX_RESULTS,
    ) -> List[Dict]:
        """Find symbol definitions and usages through Python AST analysis."""
        results: List[Dict] = []

        for py_path in self.repo_path.rglob("*.py"):
            if self._skip(py_path):
                continue

            rel = str(py_path.relative_to(self.repo_path))
            try:
                source = py_path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source)
                src_lines = source.splitlines()
            except (SyntaxError, RecursionError, Exception):
                # Fall back to lexical for unparseable files
                for r in self.lexical_search(symbol, f"*.py", max_results=5):
                    if r.get("file") == rel:
                        r["match_type"] = "lexical_fallback"
                        results.append(r)
                        if len(results) >= max_results:
                            return results
                continue

            for node in ast.walk(tree):
                hit, mtype = self._check_node(node, symbol, symbol_type)
                if not hit or not hasattr(node, "lineno"):
                    continue

                line_no: int = node.lineno  # type: ignore[assignment]
                content = src_lines[line_no - 1] if line_no <= len(src_lines) else ""

                results.append({
                    "file": rel,
                    "line": line_no,
                    "content": content.strip(),
                    "match_type": mtype,
                    "symbol": symbol,
                })

                if len(results) >= max_results:
                    return results

        return results

    @staticmethod
    def _check_node(node: ast.AST, symbol: str, stype: str):
        """Return (matched, match_type) for a given AST node."""
        if stype in ("any", "function") and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == symbol:
                return True, "function_definition"

        if stype in ("any", "class") and isinstance(node, ast.ClassDef):
            if node.name == symbol:
                return True, "class_definition"

        if stype in ("any", "call") and isinstance(node, ast.Call):
            name = (
                node.func.id if isinstance(node.func, ast.Name) else
                node.func.attr if isinstance(node.func, ast.Attribute) else None
            )
            if name == symbol:
                return True, "function_call"

        if stype in ("any", "import") and isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == symbol or alias.asname == symbol:
                    return True, "import"

        if stype in ("any", "import") and isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == symbol or alias.asname == symbol:
                    return True, "import_from"

        if stype in ("any", "variable") and isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == symbol:
                    return True, "variable_assignment"

        return False, ""

    # ======================================================================
    # LAYER 3 — SEMANTIC (TF-IDF)
    # ======================================================================

    def build_tfidf_index(self) -> bool:
        """Index file chunks with TF-IDF. Returns True on success."""
        if not _HAS_SKLEARN:
            return False

        docs: List[str] = []
        meta: List[Dict] = []

        for path in self._iter_text_files():
            rel = str(path.relative_to(self.repo_path))
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                all_lines = text.splitlines()

                for i in range(0, len(all_lines), _CHUNK_LINES):
                    chunk = "\n".join(all_lines[i: i + _CHUNK_LINES]).strip()
                    if chunk:
                        docs.append(chunk)
                        meta.append({
                            "file": rel,
                            "start_line": i + 1,
                            "end_line": min(i + _CHUNK_LINES, len(all_lines)),
                        })
            except Exception:
                pass

        if not docs:
            return False

        try:
            vec = TfidfVectorizer(
                max_features=15_000,
                stop_words="english",
                ngram_range=(1, 2),
                sublinear_tf=True,
            )
            matrix = vec.fit_transform(docs)
            self._vectorizer = vec
            self._tfidf_matrix = matrix
            self._tfidf_docs = docs
            self._tfidf_meta = meta
            return True
        except Exception:
            return False

    def semantic_search(self, query: str, max_results: int = 10) -> List[Dict]:
        """Find semantically similar code chunks using TF-IDF cosine similarity."""
        if not _HAS_SKLEARN:
            return [{"info": "scikit-learn not installed — semantic search unavailable."}]

        if self._tfidf_matrix is None:
            if not self.build_tfidf_index():
                return []

        try:
            q_vec = self._vectorizer.transform([query])   # type: ignore[union-attr]
            sims = cosine_similarity(q_vec, self._tfidf_matrix)[0]  # type: ignore[arg-type]
            top_idx = sims.argsort()[-max_results:][::-1]

            results = []
            for idx in top_idx:
                score = float(sims[idx])
                if score < 0.05:
                    break
                meta = self._tfidf_meta[idx]            # type: ignore[index]
                chunk = self._tfidf_docs[idx]           # type: ignore[index]
                results.append({
                    "file": meta["file"],
                    "start_line": meta["start_line"],
                    "end_line": meta["end_line"],
                    "content": chunk[:300] + ("..." if len(chunk) > 300 else ""),
                    "score": round(score, 4),
                    "match_type": "semantic",
                })
            return results
        except Exception as exc:
            return [{"error": str(exc)}]

    # ======================================================================
    # COMBINED — auto-selects layers until sufficient evidence is found
    # ======================================================================

    def retrieve(
        self,
        query: str,
        layer: str = "auto",
        max_results: int = _MAX_RESULTS,
    ) -> Dict[str, Any]:
        """
        Multi-layer retrieval. Tries layers in order; falls through if results are sparse.
        layer: "lexical" | "structural" | "semantic" | "auto"
        """
        out: Dict[str, Any] = {
            "query": query,
            "layers_used": [],
            "results": [],
        }

        def _add(results: List[Dict], layer_name: str):
            if results:
                out["layers_used"].append(layer_name)
                out["results"].extend(results)

        if layer in ("lexical", "auto"):
            _add(self.lexical_search(query, max_results=max_results), "lexical")

        if layer in ("structural", "auto") and len(out["results"]) < 5:
            _add(self.structural_search(query), "structural")

        if layer in ("semantic", "auto") and len(out["results"]) < 3:
            _add(self.semantic_search(query), "semantic")

        # Deduplicate by (file, line)
        seen: Set[tuple] = set()
        unique: List[Dict] = []
        for r in out["results"]:
            key = (r.get("file", ""), r.get("line", r.get("start_line", 0)))
            if key not in seen:
                seen.add(key)
                unique.append(r)

        out["results"] = unique
        out["total"] = len(unique)
        return out

    # ======================================================================
    # Helpers
    # ======================================================================

    def _skip(self, path: Path) -> bool:
        return (
            any(part in _IGNORE_DIRS for part in path.parts)
            or not path.is_file()
            or path.stat().st_size > _MAX_FILE_SIZE
        )

    def _iter_text_files(self):
        for path in self.repo_path.rglob("*"):
            if self._skip(path):
                continue
            if path.suffix.lower() not in _TEXT_EXTS:
                continue
            yield path

#!/usr/bin/env python3
"""
Code Analyzer — AST-based codebase analysis for Python + regex-based analysis for JS/TS.
Builds relationship graphs, extracts code structure, detects languages and frameworks.
"""

import ast
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IGNORE_DIRS: Set[str] = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "dist", "build", ".next", ".tox", ".eggs", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "coverage", ".nyc_output",
}

LANGUAGE_EXTENSIONS: Dict[str, str] = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript/React", ".jsx": "JavaScript/React",
    ".java": "Java", ".go": "Go", ".rs": "Rust",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".c": "C", ".h": "C/C++", ".hpp": "C++",
    ".rb": "Ruby", ".php": "PHP", ".cs": "C#",
    ".swift": "Swift", ".kt": "Kotlin", ".scala": "Scala",
    ".sh": "Shell", ".bash": "Shell",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".sql": "SQL", ".md": "Markdown",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML", ".xml": "XML",
}

FRAMEWORK_SIGNATURES: Dict[str, List[str]] = {
    "Django": ["django.conf", "INSTALLED_APPS", "django.urls", "wsgi.py", "asgi.py"],
    "Flask": ["from flask import", "Flask(__name__", "@app.route", "flask.Flask"],
    "FastAPI": ["from fastapi", "FastAPI()", "@app.get(", "@router.get("],
    "React": ["from 'react'", "from \"react\"", "useState", "useEffect", "ReactDOM", "React."],
    "Vue.js": ["createApp(", "defineComponent(", "from 'vue'"],
    "Angular": ["@Component", "@NgModule", "from '@angular"],
    "Express": ["require('express')", "express()", "app.listen("],
    "Next.js": ["getServerSideProps", "getStaticProps", "from 'next'"],
    "Vite": ["from 'vite'", "createVite", "vite.config", "@vitejs"],
    "TailwindCSS": ["tailwindcss", "tailwind.config"],
    "Spring": ["@Controller", "@Service", "@RestController", "springframework"],
    "Rails": ["ActionController", "ApplicationRecord", "has_many", "belongs_to"],
    "Pytest": ["import pytest", "def test_", "@pytest.fixture"],
    "SQLAlchemy": ["from sqlalchemy", "declarative_base", "Column("],
    "Google Gemini GenAI": ["@google/genai", "GoogleGenAI", "gemini-2", "gemini-1.5"],
}

MAX_FILE_SIZE = 500 * 1024   # 500 KB — skip larger files
MAX_PY_FILES = 100           # cap for full AST parsing
MAX_JS_FILES = 60            # cap for regex parsing


# ---------------------------------------------------------------------------
# AST visitor helpers
# ---------------------------------------------------------------------------

class _ScopeVisitor(ast.NodeVisitor):
    """Single-pass visitor that collects classes, functions, imports, and calls."""

    def __init__(self, source_lines: List[str]):
        self.lines = source_lines
        self.classes: List[ast.ClassDef] = []
        self.top_functions: List[ast.FunctionDef] = []
        self.imports: List[ast.stmt] = []
        self.calls: List[Dict[str, Any]] = []
        self._class_depth = 0

    def visit_ClassDef(self, node: ast.ClassDef):
        self.classes.append(node)
        self._class_depth += 1
        self.generic_visit(node)
        self._class_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if self._class_depth == 0:
            self.top_functions.append(node)
        # Collect calls inside this function
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = self._call_name(child)
                if name:
                    self.calls.append({"name": name, "line": getattr(child, "lineno", 0)})
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Import(self, node: ast.Import):
        self.imports.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        self.imports.append(node)

    @staticmethod
    def _call_name(node: ast.Call) -> Optional[str]:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None


# ---------------------------------------------------------------------------
# CodeAnalyzer
# ---------------------------------------------------------------------------

class CodeAnalyzer:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_structure(self, max_depth: int = 6) -> Dict:
        """Return the directory tree as a nested dict."""
        return self._build_tree(self.repo_path, depth=0, max_depth=max_depth)

    def get_metadata(self) -> Dict:
        """Detect languages, frameworks, entry points, and dependencies."""
        lang_counts: Dict[str, int] = defaultdict(int)
        total_files = 0
        total_lines = 0

        for path in self._iter_files():
            ext = path.suffix.lower()
            lang = LANGUAGE_EXTENSIONS.get(ext)
            if lang:
                lang_counts[lang] += 1
                total_files += 1
                try:
                    total_lines += sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
                except Exception:
                    pass

        frameworks = self._detect_frameworks()
        entry_points = self._detect_entry_points()
        key_files = self._detect_key_files()
        deps = self._parse_dependencies()

        return {
            "languages": dict(lang_counts),
            "primary_language": max(lang_counts, key=lang_counts.get) if lang_counts else "Unknown",
            "frameworks": frameworks,
            "entry_points": entry_points,
            "key_config_files": key_files,
            "total_files": total_files,
            "total_lines": total_lines,
            "dependencies": deps,
        }

    def build_graph(self) -> Dict:
        """
        Build a node-edge relationship graph of the codebase.
        Nodes: files, classes, top-level functions, external modules.
        Edges: contains, imports, calls, extends.
        """
        nodes: Dict[str, Dict] = {}
        edges: List[Dict] = []
        _id_counter = [0]

        def new_id(prefix: str) -> str:
            _id_counter[0] += 1
            return f"{prefix}_{_id_counter[0]}"

        file_id: Dict[str, str] = {}        # rel_path -> node_id
        func_id: Dict[str, str] = {}        # rel_path::name -> node_id
        class_id: Dict[str, str] = {}       # rel_path::name -> node_id
        module_id: Dict[str, str] = {}      # module_name -> node_id

        def ensure_module(mod_name: str) -> str:
            if mod_name not in module_id:
                nid = new_id("mod")
                module_id[mod_name] = nid
                nodes[nid] = {
                    "id": nid, "label": mod_name,
                    "type": "module", "path": mod_name,
                }
            return module_id[mod_name]

        def add_edge(src: str, tgt: str, etype: str):
            edges.append({"source": src, "target": tgt, "type": etype})

        # ---------- Python files ----------
        py_files = list(self._iter_files(ext=".py"))[:MAX_PY_FILES]
        for py_path in py_files:
            rel = str(py_path.relative_to(self.repo_path))

            nid = new_id("file")
            file_id[rel] = nid
            nodes[nid] = {
                "id": nid, "label": py_path.name,
                "type": "file", "path": rel, "language": "python",
            }

            try:
                source = py_path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source)
                lines = source.splitlines()
                visitor = _ScopeVisitor(lines)
                visitor.visit(tree)

                # Classes
                for cls in visitor.classes:
                    cid = new_id("cls")
                    key = f"{rel}::{cls.name}"
                    class_id[key] = cid
                    nodes[cid] = {
                        "id": cid, "label": cls.name,
                        "type": "class", "path": rel, "line": cls.lineno,
                    }
                    add_edge(nid, cid, "contains")
                    # extends
                    for base in cls.bases:
                        base_name = (
                            base.id if isinstance(base, ast.Name) else
                            base.attr if isinstance(base, ast.Attribute) else None
                        )
                        if base_name:
                            add_edge(cid, ensure_module(base_name), "extends")

                # Top-level functions
                for fn in visitor.top_functions:
                    fid = new_id("fn")
                    key = f"{rel}::{fn.name}"
                    func_id[key] = fid
                    nodes[fid] = {
                        "id": fid, "label": fn.name,
                        "type": "function", "path": rel, "line": fn.lineno,
                    }
                    add_edge(nid, fid, "contains")

                # Imports
                for imp in visitor.imports:
                    if isinstance(imp, ast.Import):
                        for alias in imp.names:
                            root_mod = alias.name.split(".")[0]
                            # Check if it's a local file
                            local = self.repo_path / (alias.name.replace(".", "/") + ".py")
                            if local.exists():
                                local_rel = str(local.relative_to(self.repo_path))
                                if local_rel in file_id:
                                    add_edge(nid, file_id[local_rel], "imports")
                                    continue
                            add_edge(nid, ensure_module(root_mod), "imports")

                    elif isinstance(imp, ast.ImportFrom) and imp.module:
                        root_mod = imp.module.split(".")[0]
                        local = self.repo_path / (imp.module.replace(".", "/") + ".py")
                        if local.exists():
                            local_rel = str(local.relative_to(self.repo_path))
                            if local_rel in file_id:
                                add_edge(nid, file_id[local_rel], "imports")
                                continue
                        add_edge(nid, ensure_module(root_mod), "imports")

            except (SyntaxError, RecursionError, Exception):
                pass

        # ---------- JS / TS files ----------
        js_files: List[Path] = []
        for ext in (".js", ".ts", ".jsx", ".tsx"):
            js_files.extend(self._iter_files(ext=ext))
        js_files = js_files[:MAX_JS_FILES]

        for js_path in js_files:
            rel = str(js_path.relative_to(self.repo_path))
            if rel in file_id:
                continue

            nid = new_id("file")
            file_id[rel] = nid
            nodes[nid] = {
                "id": nid, "label": js_path.name,
                "type": "file", "path": rel, "language": "javascript",
            }

            try:
                source = js_path.read_text(encoding="utf-8", errors="replace")

                # ESM imports
                for m in re.finditer(r"""(?:import|export)\s+(?:(?:[\w*\s{},]+)\s+from\s+)?['"]([^'"]+)['"]""", source):
                    mod = m.group(1)
                    if mod.startswith("."):
                        # Resolve local relative file import
                        try:
                            target_dir = js_path.parent
                            target_base = target_dir / mod
                            resolved_rel = None
                            for test_suffix in ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js"):
                                test_path = Path(str(target_base) + test_suffix).resolve()
                                if test_path.exists() and test_path.is_file():
                                    try:
                                        rel_p = str(test_path.relative_to(self.repo_path.resolve()))
                                        resolved_rel = rel_p
                                        break
                                    except ValueError:
                                        pass
                            if resolved_rel:
                                if resolved_rel not in file_id:
                                    target_nid = new_id("file")
                                    file_id[resolved_rel] = target_nid
                                    nodes[target_nid] = {
                                        "id": target_nid, "label": Path(resolved_rel).name,
                                        "type": "file", "path": resolved_rel, "language": "javascript",
                                    }
                                add_edge(nid, file_id[resolved_rel], "imports")
                                continue
                        except Exception:
                            pass
                    root_mod = mod.lstrip("@").split("/")[0]
                    add_edge(nid, ensure_module(root_mod), "imports")

                # Named function declarations
                for m in re.finditer(
                    r"(?:^|\n)(?:export\s+)?(?:async\s+)?function\s+(\w+)",
                    source, re.MULTILINE
                ):
                    fn_name = m.group(1)
                    fid = new_id("fn")
                    nodes[fid] = {
                        "id": fid, "label": fn_name, "type": "function",
                        "path": rel, "line": source[: m.start()].count("\n") + 1,
                    }
                    add_edge(nid, fid, "contains")

                # Arrow / const functions
                for m in re.finditer(
                    r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\(",
                    source
                ):
                    fn_name = m.group(1)
                    fid = new_id("fn")
                    nodes[fid] = {
                        "id": fid, "label": fn_name, "type": "function",
                        "path": rel, "line": source[: m.start()].count("\n") + 1,
                    }
                    add_edge(nid, fid, "contains")

            except Exception:
                pass

        # Deduplicate edges
        seen: Set[tuple] = set()
        unique_edges = []
        for e in edges:
            key = (e["source"], e["target"], e["type"])
            if key not in seen:
                seen.add(key)
                unique_edges.append(e)

        return {
            "nodes": list(nodes.values()),
            "edges": unique_edges,
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(unique_edges),
                "files": len(file_id),
                "functions": len(func_id),
                "classes": len(class_id),
                "modules": len(module_id),
            },
        }

    def trace_execution_flow(self, entry_point: str, max_depth: int = 5) -> List[Dict]:
        """Walk the AST call graph starting from entry_point."""
        # Build call graph: func_name -> list of {name, line, file}
        call_graph: Dict[str, List[Dict]] = defaultdict(list)

        for py_path in self._iter_files(ext=".py"):
            rel = str(py_path.relative_to(self.repo_path))
            try:
                source = py_path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source)

                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    fn_name = node.name
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            cname = None
                            if isinstance(child.func, ast.Name):
                                cname = child.func.id
                            elif isinstance(child.func, ast.Attribute):
                                cname = child.func.attr
                            if cname:
                                call_graph[fn_name].append(
                                    {"name": cname, "line": child.lineno, "file": rel}
                                )
            except Exception:
                pass

        # Trace
        trace: List[Dict] = []
        visited: Set[str] = set()

        def _trace(fn: str, depth: int):
            if depth > max_depth or fn in visited:
                return
            visited.add(fn)
            calls = call_graph.get(fn, [])[:8]
            trace.append({"function": fn, "depth": depth, "calls": calls})
            for c in calls:
                _trace(c["name"], depth + 1)

        _trace(entry_point, 0)
        return trace

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _iter_files(self, ext: Optional[str] = None):
        for path in self.repo_path.rglob("*"):
            if not path.is_file():
                continue
            if any(part in IGNORE_DIRS for part in path.parts):
                continue
            if path.stat().st_size > MAX_FILE_SIZE:
                continue
            if ext and path.suffix.lower() != ext:
                continue
            yield path

    def _build_tree(self, path: Path, depth: int, max_depth: int) -> Optional[Dict]:
        if depth > max_depth:
            return None
        if path.name in IGNORE_DIRS:
            return None
        # Skip hidden dirs except .github
        if path.name.startswith(".") and path.name != ".github" and path.is_dir():
            return None

        if path.is_file():
            ext = path.suffix.lower()
            return {
                "name": path.name,
                "type": "file",
                "path": str(path.relative_to(self.repo_path)),
                "language": LANGUAGE_EXTENSIONS.get(ext, ""),
                "size": path.stat().st_size,
            }

        if path.is_dir():
            children = []
            try:
                for child in sorted(path.iterdir()):
                    node = self._build_tree(child, depth + 1, max_depth)
                    if node:
                        children.append(node)
            except PermissionError:
                pass
            return {
                "name": path.name,
                "type": "directory",
                "path": str(path.relative_to(self.repo_path)) if path != self.repo_path else ".",
                "children": children,
            }
        return None

    def analyze_file(self, file_path: str) -> Dict[str, Any]:
        """Analyze a specific file to extract structure, classes, functions, imports, and exports."""
        path = (self.repo_path / file_path).resolve()
        if not path.exists() or not str(path).startswith(str(self.repo_path)):
            return {"error": "File not found or access denied."}

        ext = path.suffix.lower()
        lang = LANGUAGE_EXTENSIONS.get(ext, "Text")
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"error": f"Could not read file: {e}"}

        lines = source.splitlines()
        result: Dict[str, Any] = {
            "file_path": str(path.relative_to(self.repo_path)),
            "language": lang,
            "total_lines": len(lines),
            "size_bytes": path.stat().st_size,
            "classes": [],
            "functions": [],
            "imports": [],
            "exports": [],
            "dependencies": [],
        }

        if ext == ".py":
            try:
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        result["classes"].append({
                            "name": node.name,
                            "line": node.lineno,
                            "docstring": ast.get_docstring(node),
                        })
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        result["functions"].append({
                            "name": node.name,
                            "line": node.lineno,
                            "docstring": ast.get_docstring(node),
                        })
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            result["imports"].append({"module": alias.name, "line": node.lineno})
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            result["imports"].append({"module": node.module, "line": node.lineno})
            except Exception:
                pass
        elif ext in (".js", ".jsx", ".ts", ".tsx"):
            # Functions
            for m in re.finditer(r"(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_$]+)", source):
                result["functions"].append({
                    "name": m.group(1),
                    "line": source[:m.start()].count("\n") + 1
                })
            for m in re.finditer(r"(?:export\s+)?const\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", source):
                result["functions"].append({
                    "name": m.group(1),
                    "line": source[:m.start()].count("\n") + 1
                })
            # Classes / Interfaces / Types
            for m in re.finditer(r"(?:export\s+)?(?:class|interface|type)\s+([a-zA-Z0-9_$]+)", source):
                result["classes"].append({
                    "name": m.group(1),
                    "line": source[:m.start()].count("\n") + 1
                })
            # Imports
            for m in re.finditer(r"""import\s+(?:.*?from\s+)?['"]([^'"]+)['"]""", source):
                result["imports"].append({
                    "module": m.group(1),
                    "line": source[:m.start()].count("\n") + 1
                })
            # Exports
            for m in re.finditer(r"export\s+(?:default\s+)?(?:function|class|const|let|var|type|interface)?\s*([a-zA-Z0-9_$]+)?", source):
                if m.group(1):
                    result["exports"].append({"name": m.group(1), "line": source[:m.start()].count("\n") + 1})

        return result

    def _detect_frameworks(self) -> List[str]:
        found = set()
        deps = self._parse_dependencies()

        # Check package.json dependencies
        node_deps = set(deps.get("node", []) + deps.get("node_dev", []))
        if any("react" in d for d in node_deps):
            found.add("React")
        if any("vite" in d for d in node_deps):
            found.add("Vite")
        if any("next" in d for d in node_deps):
            found.add("Next.js")
        if any("vue" in d for d in node_deps):
            found.add("Vue.js")
        if any("angular" in d for d in node_deps):
            found.add("Angular")
        if any("express" in d for d in node_deps):
            found.add("Express")
        if any("tailwind" in d for d in node_deps):
            found.add("TailwindCSS")
        if any("google/genai" in d or "google-generativeai" in d for d in node_deps):
            found.add("Google Gemini GenAI")

        # Check python dependencies
        py_deps = [d.lower() for d in deps.get("python", [])]
        if any("django" in d for d in py_deps):
            found.add("Django")
        if any("flask" in d for d in py_deps):
            found.add("Flask")
        if any("fastapi" in d for d in py_deps):
            found.add("FastAPI")
        if any("google-generativeai" in d or "google-genai" in d for d in py_deps):
            found.add("Google Gemini GenAI")
        if any("openai" in d for d in py_deps):
            found.add("OpenAI")
        if any("anthropic" in d for d in py_deps):
            found.add("Anthropic Claude")
        if any("sqlalchemy" in d for d in py_deps):
            found.add("SQLAlchemy")
        if any("pytest" in d for d in py_deps):
            found.add("Pytest")

        # Content signature scanning across code files
        sample_files: List[Path] = []
        for ext in (".py", ".js", ".ts", ".tsx", ".jsx", ".html"):
            sample_files.extend(list(self._iter_files(ext=ext))[:40])

        content_blob = ""
        for f in sample_files[:100]:
            try:
                content_blob += f.read_text(encoding="utf-8", errors="replace")[:2000]
            except Exception:
                pass

        for fw, sigs in FRAMEWORK_SIGNATURES.items():
            if any(sig in content_blob for sig in sigs):
                found.add(fw)

        return sorted(found)

    def _detect_entry_points(self) -> List[str]:
        candidates = [
            "App.tsx", "main.tsx", "index.tsx", "App.jsx", "main.jsx", "index.jsx", "App.js",
            "src/App.tsx", "src/main.tsx", "src/index.tsx", "src/App.jsx", "src/main.jsx", "src/index.jsx",
            "main.py", "app.py", "index.py", "run.py", "manage.py", "server.py", "wsgi.py", "asgi.py",
            "src/main.py", "src/app.py", "src/server.py",
            "index.js", "main.js", "app.js", "server.js",
            "index.ts", "main.ts", "server.ts",
            "main.go", "main.rs", "Main.java", "index.html",
        ]
        found = []
        for c in candidates:
            if (self.repo_path / c).exists():
                found.append(c)
        return found

    def _detect_key_files(self) -> List[str]:
        candidates = [
            "requirements.txt", "package.json", "Cargo.toml",
            "go.mod", "pom.xml", "build.gradle",
            "setup.py", "pyproject.toml", "setup.cfg",
            "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
            ".github", "Makefile", "tox.ini", "pytest.ini",
            ".eslintrc.json", "tsconfig.json", "vite.config.ts", "vite.config.js",
            ".env.example", ".env.local.example",
        ]
        return [c for c in candidates if (self.repo_path / c).exists()]

    def _parse_dependencies(self) -> Dict[str, List[str]]:
        deps: Dict[str, List[str]] = {}

        # Python — requirements.txt
        req = self.repo_path / "requirements.txt"
        if req.exists():
            try:
                lines = req.read_text().splitlines()
                deps["python"] = [
                    l.strip() for l in lines
                    if l.strip() and not l.startswith("#") and not l.startswith("-")
                ]
            except Exception:
                pass

        # Python — pyproject.toml (basic parse)
        ppt = self.repo_path / "pyproject.toml"
        if ppt.exists() and "python" not in deps:
            try:
                content = ppt.read_text()
                matches = re.findall(r'"([a-zA-Z0-9_\-]+)[>=<!\[]', content)
                deps["python"] = list(dict.fromkeys(matches))[:30]
            except Exception:
                pass

        # Node — package.json
        pkg = self.repo_path / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                deps["node"] = list(data.get("dependencies", {}).keys())
                deps["node_dev"] = list(data.get("devDependencies", {}).keys())
            except Exception:
                pass

        return deps

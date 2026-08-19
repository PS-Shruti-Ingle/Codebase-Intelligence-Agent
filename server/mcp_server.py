#!/usr/bin/env python3
"""
MCP Server — Codebase Intelligence Agent
==========================================
Exposes repository analysis capabilities via the Model Context Protocol.

Tools      : clone_repository, get_repo_structure, read_file, search_code,
             find_references, trace_execution, check_repo_status,
             sync_repository, build_relationship_graph, semantic_search
Resources  : repo://structure, repo://readme, repo://metadata, repo://git_status
Prompts    : architecture_analysis, execution_flow, code_review, module_explanation
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Dict

# ── ensure sibling modules are importable ────────────────────────────────────
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from code_analyzer import CodeAnalyzer, IGNORE_DIRS, LANGUAGE_EXTENSIONS
from retrieval import RetrievalEngine

# ── MCP import — supports mcp 2.x (MCPServer) and older (FastMCP) ────────────
try:
    # mcp >= 2.0: FastMCP moved to mcp.server.mcpserver as MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "mcp", "-q"])
        from mcp.server.mcpserver import MCPServer as FastMCP

# ── gitpython ────────────────────────────────────────────────────────────────
try:
    import git
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "gitpython", "-q"])
    import git

# ── Init ─────────────────────────────────────────────────────────────────────
mcp = FastMCP("Codebase Intelligence Agent")

# ── Per-repo instance caches (keyed by resolved repo path string) ─────────────
# Avoids rebuilding AST graphs and TF-IDF index on every tool call.
_analyzer_cache: Dict[str, CodeAnalyzer]   = {}
_retrieval_cache: Dict[str, RetrievalEngine] = {}


def _get_analyzer(repo: Path) -> CodeAnalyzer:
    key = str(repo.resolve())
    if key not in _analyzer_cache:
        _analyzer_cache[key] = CodeAnalyzer(key)
    return _analyzer_cache[key]


def _get_retrieval(repo: Path) -> RetrievalEngine:
    key = str(repo.resolve())
    if key not in _retrieval_cache:
        _retrieval_cache[key] = RetrievalEngine(key)
    return _retrieval_cache[key]


def _invalidate_cache(repo: Path):
    """Call after sync or re-clone so stale cached objects are dropped."""
    key = str(repo.resolve())
    _analyzer_cache.pop(key, None)
    _retrieval_cache.pop(key, None)

# Persistent state file – survives server restarts
_STATE_FILE = _HERE.parent / "repos" / ".state.json"


# ===========================================================================
# State helpers
# ===========================================================================

def _load_state() -> dict:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except Exception:
            pass
    return {"repo_path": None, "repo_url": None, "branch": None}


def _save_state(state: dict):
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def _current_repo() -> Optional[Path]:
    state = _load_state()
    rp = state.get("repo_path")
    if rp and Path(rp).exists():
        return Path(rp)
    return None


def _lang(suffix: str) -> str:
    return LANGUAGE_EXTENSIONS.get(suffix.lower(), "text")


# ===========================================================================
# TOOLS
# ===========================================================================

@mcp.tool()
def clone_repository(url: str, branch: str = "main") -> str:
    """
    Clone a public GitHub repository to local storage.
    Returns JSON with status, repo_path, and repo_name.
    """
    repos_dir = _HERE.parent / "repos"
    repos_dir.mkdir(parents=True, exist_ok=True)

    # Derive a safe directory name
    repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    repo_path = repos_dir / repo_name

    # If already cloned with same remote, just update state
    if repo_path.exists():
        try:
            existing = git.Repo(repo_path)
            remote_url = existing.remotes.origin.url.rstrip("/").removesuffix(".git")
            target_url = url.rstrip("/").removesuffix(".git")
            if remote_url == target_url:
                _save_state({"repo_path": str(repo_path), "repo_url": url, "branch": branch})
                return json.dumps({
                    "status": "already_exists",
                    "message": f"Repository already cloned at {repo_path}",
                    "repo_path": str(repo_path),
                    "repo_name": repo_name,
                })
        except Exception:
            pass
        # Different repo — suffix timestamp
        import time
        repo_path = repos_dir / f"{repo_name}_{int(time.time())}"

    # Attempt clone
    def _clone(br: Optional[str]):
        kwargs = {}
        if br and br not in ("main", "master", "default"):
            kwargs["branch"] = br
        git.Repo.clone_from(url, repo_path, **kwargs)

    try:
        _clone(branch)
    except git.exc.GitCommandError:
        try:
            # Retry without branch (use remote default)
            git.Repo.clone_from(url, repo_path)
            branch = "default"
        except Exception as exc:
            if repo_path.exists():
                import shutil
                shutil.rmtree(repo_path, ignore_errors=True)
            return json.dumps({"status": "error", "message": str(exc)})

    # Resolve the actual checked-out branch name
    try:
        resolved_branch = git.Repo(repo_path).active_branch.name
    except Exception:
        resolved_branch = branch

    _save_state({"repo_path": str(repo_path), "repo_url": url, "branch": resolved_branch})
    _invalidate_cache(repo_path)  # fresh clone — drop any stale cache
    return json.dumps({
        "status": "success",
        "message": "Repository cloned successfully",
        "repo_path": str(repo_path),
        "repo_name": repo_name,
        "resolved_branch": resolved_branch,
    })


@mcp.tool()
def get_repo_structure(max_depth: int = 6) -> str:
    """
    Return the directory tree of the loaded repository as JSON.
    Skips common build / dependency directories automatically.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded. Use clone_repository first."})

    analyzer = _get_analyzer(repo)
    return json.dumps(analyzer.get_structure(max_depth=max_depth), indent=2)


@mcp.tool()
def read_file(file_path: str, start_line: int = 1, end_line: Optional[int] = None) -> str:
    """
    Read a repository file and return its content with line numbers.
    file_path is relative to the repository root.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    # Resolve and validate path
    if Path(file_path).is_absolute():
        full = Path(file_path).resolve()
    else:
        full = (repo / file_path).resolve()

    try:
        if not str(full).startswith(str(repo.resolve())):
            return json.dumps({"error": "Access denied: path is outside the repository."})
    except Exception:
        pass

    if not full.exists():
        return json.dumps({"error": f"File not found: {file_path}"})
    if full.stat().st_size > 2 * 1024 * 1024:
        return json.dumps({"error": "File too large to display (> 2 MB)."})

    try:
        all_lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, start_line) - 1
        end = len(all_lines) if end_line is None else min(end_line, len(all_lines))

        numbered = [{"line": i + start + 1, "content": line}
                    for i, line in enumerate(all_lines[start:end])]

        return json.dumps({
            "file_path": str(full.relative_to(repo)),
            "total_lines": len(all_lines),
            "shown_lines": {"from": start + 1, "to": end},
            "language": _lang(full.suffix),
            "content": numbered,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def search_code(
    query: str,
    file_pattern: str = "**/*",
    is_regex: bool = False,
    case_sensitive: bool = False,
) -> str:
    """
    Lexical search (Layer 1) — grep-style search across all text files.
    Supports exact strings or regular expressions.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    engine = _get_retrieval(repo)
    results = engine.lexical_search(query, file_pattern, is_regex, case_sensitive)
    return json.dumps({"query": query, "results": results, "total": len(results)}, indent=2)


@mcp.tool()
def find_references(symbol: str, symbol_type: str = "any") -> str:
    """
    Structural search (Layer 2) — find definitions and usages of a symbol via AST.
    symbol_type: any | function | class | variable | import | call
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    engine = _get_retrieval(repo)
    results = engine.structural_search(symbol, symbol_type)
    return json.dumps({"symbol": symbol, "results": results, "total": len(results)}, indent=2)


@mcp.tool()
def trace_execution(entry_point: str, max_depth: int = 5) -> str:
    """
    Trace the call graph starting from a given function name.
    Returns a depth-ordered list of function → calls.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    analyzer = _get_analyzer(repo)
    trace = analyzer.trace_execution_flow(entry_point, max_depth)
    return json.dumps({"entry_point": entry_point, "execution_flow": trace}, indent=2)


@mcp.tool()
def check_repo_status() -> str:
    """
    Check git status: current branch, commits ahead/behind remote,
    uncommitted changes, and recent commit history.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    try:
        r = git.Repo(repo)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    # Fetch silently (ignore network errors)
    try:
        r.remotes.origin.fetch()
    except Exception:
        pass

    branch = r.active_branch.name

    try:
        ahead  = len(list(r.iter_commits(f"origin/{branch}..{branch}")))
        behind = len(list(r.iter_commits(f"{branch}..origin/{branch}")))
    except Exception:
        ahead = behind = 0

    commits = []
    for c in list(r.iter_commits(max_count=5)):
        commits.append({
            "sha": c.hexsha[:7],
            "message": c.message.strip().split("\n")[0],
            "author": str(c.author),
            "date": c.committed_datetime.isoformat(),
        })

    changed = [item.a_path for item in r.index.diff(None)]
    state = _load_state()

    return json.dumps({
        "repo_url": state.get("repo_url", ""),
        "branch": branch,
        "ahead_count": ahead,
        "behind_count": behind,
        "needs_sync": behind > 0,
        "has_uncommitted": bool(changed),
        "changed_files": changed,
        "untracked_files": r.untracked_files[:10],
        "recent_commits": commits,
    }, indent=2)


@mcp.tool()
def sync_repository(confirmed: bool = False) -> str:
    """
    Pull remote changes into the local repository.
    MUST be called with confirmed=True — never syncs without explicit consent.
    Blocks if there are uncommitted local changes to avoid data loss.
    """
    if not confirmed:
        return json.dumps({
            "status": "confirmation_required",
            "message": (
                "Syncing will pull remote commits into your local copy. "
                "Call again with confirmed=True to proceed."
            ),
            "warning": "Uncommitted local changes will NOT be overwritten — sync is blocked if any exist.",
        })

    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    try:
        r = git.Repo(repo)
        if r.is_dirty():
            return json.dumps({
                "status": "blocked",
                "message": "Repository has uncommitted changes. Stash or commit them before syncing.",
                "changed_files": [item.a_path for item in r.index.diff(None)],
            })

        pull_info = r.remotes.origin.pull()
        _invalidate_cache(repo)  # repo contents changed — drop cached instances
        return json.dumps({
            "status": "success",
            "message": "Repository synced successfully.",
            "refs_updated": [str(info.ref) for info in pull_info],
        })
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)})


@mcp.tool()
def build_relationship_graph() -> str:
    """
    Build a node-edge relationship graph of the codebase.
    Nodes: files, classes, functions, external modules.
    Edges: contains, imports, calls, extends.
    Returns JSON suitable for D3.js force-directed rendering.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    analyzer = _get_analyzer(repo)
    graph = analyzer.build_graph()
    return json.dumps(graph, indent=2)


@mcp.tool()
def semantic_search(query: str) -> str:
    """
    Semantic search (Layer 3) — TF-IDF cosine similarity over chunked file content.
    Use when lexical / structural search yields insufficient results.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    engine = _get_retrieval(repo)
    results = engine.semantic_search(query)
    return json.dumps({"query": query, "results": results, "total": len(results)}, indent=2)


@mcp.tool()
def fetch_github_repo_info(owner: str, repo: str) -> str:
    """
    Fetch basic repository metadata (stars, forks, open issues) from the public GitHub API.
    Demonstrates using a free public API and robust HTTP error handling.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}"
    req = urllib.request.Request(url, headers={'User-Agent': 'CodebaseIntelligenceAgent'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            return json.dumps({
                "success": True,
                "owner": owner,
                "repo": repo,
                "stars": data.get("stargazers_count"),
                "forks": data.get("forks_count"),
                "open_issues": data.get("open_issues_count"),
                "description": data.get("description")
            }, indent=2)
    except urllib.error.HTTPError as e:
        error_type = "not_found" if e.code == 404 else "rate_limit" if e.code == 403 else "http_error"
        return json.dumps({
            "success": False,
            "error_type": error_type,
            "code": e.code,
            "message": e.reason
        })
    except urllib.error.URLError as e:
        return json.dumps({
            "success": False,
            "error_type": "network_error",
            "message": str(e.reason)
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "error_type": "unknown_error",
            "message": str(e)
        })


# ===========================================================================
# RESOURCES
# ===========================================================================

@mcp.resource("repo://structure")
def resource_structure() -> str:
    """Full directory tree of the loaded repository (JSON)."""
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded"})
    return json.dumps(_get_analyzer(repo).get_structure(), indent=2)


@mcp.resource("repo://readme")
def resource_readme() -> str:
    """README content of the loaded repository."""
    repo = _current_repo()
    if not repo:
        return "No repository loaded."
    for name in ("README.md", "README.rst", "README.txt", "README"):
        f = repo / name
        if f.exists():
            return f.read_text(encoding="utf-8", errors="replace")
    return "No README found in repository root."


@mcp.resource("repo://metadata")
def resource_metadata() -> str:
    """Detected languages, frameworks, entry points, and dependencies (JSON)."""
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded"})
    return json.dumps(_get_analyzer(repo).get_metadata(), indent=2)


@mcp.resource("repo://git_status")
def resource_git_status() -> str:
    """Current branch, dirty status, and untracked files (JSON)."""
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded"})
    try:
        r = git.Repo(repo)
        return json.dumps({
            "branch": r.active_branch.name,
            "is_dirty": r.is_dirty(),
            "staged": [item.a_path for item in r.index.diff("HEAD")][:10],
            "unstaged": [item.a_path for item in r.index.diff(None)][:10],
            "untracked": r.untracked_files[:10],
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ===========================================================================
# PROMPTS
# ===========================================================================

@mcp.prompt()
def architecture_analysis(focus: str = "overall") -> str:
    """Prompt template for high-level architecture analysis of the codebase."""
    return f"""You are analyzing the architecture of a codebase. Focus area: **{focus}**.

Steps:
1. Call `get_repo_structure` to see the directory layout.
2. Call `read_file` on `repo://metadata` resource to identify languages, frameworks, and entry points.
3. Read key entry-point files with `read_file`.
4. Use `search_code` to find framework-specific patterns (routes, models, services).
5. Synthesize a clear architecture overview.

Deliverable:
- Overall design pattern (MVC, layered, microservices, etc.)
- Main components and their responsibilities
- Data flow and key request/response lifecycle
- External dependencies and integrations
- File references for every claim: [path:line]"""


@mcp.prompt()
def execution_flow(start_function: str, context: str = "") -> str:
    """Prompt template for tracing a complete execution path through the code."""
    return f"""Trace the execution flow starting from: `{start_function}`
{f"Additional context: {context}" if context else ""}

Steps:
1. Call `find_references("{start_function}", "function")` to locate the definition.
2. Call `read_file` on the file containing it.
3. Call `trace_execution("{start_function}")` for the automated call-graph.
4. Follow interesting call branches manually with more `find_references` and `read_file` calls.
5. Note any DB calls, external API calls, or async operations.

Deliverable: a numbered step-by-step execution trace with [file:line] references at each step."""


@mcp.prompt()
def code_review(file_path: str, focus: str = "general") -> str:
    """Prompt template for reviewing a specific file."""
    return f"""Review the file: `{file_path}`
Review focus: {focus}

Steps:
1. `read_file("{file_path}")` — read the full file.
2. `find_references` on any key symbols to understand context.
3. `search_code` to check how similar patterns are handled elsewhere.

Deliverable (with [file:line] citations):
- Purpose of the file / module
- Code quality observations
- Potential bugs or edge cases
- Security considerations (if applicable)
- Performance notes
- Suggestions for improvement"""


@mcp.prompt()
def module_explanation(module_name: str) -> str:
    """Prompt template for explaining what a module or file does."""
    return f"""Explain the module: `{module_name}`

Steps:
1. `search_code("{module_name}")` — find its definition and usages.
2. `find_references("{module_name}", "import")` — see who imports it.
3. `read_file` on the module itself.
4. Read a couple of its callers for usage examples.

Deliverable:
- Primary responsibility of the module
- Public API (exported functions / classes)
- How other parts of the codebase use it
- Key dependencies it relies on
- Code examples with [file:line] references"""


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    mcp.run(transport="stdio")

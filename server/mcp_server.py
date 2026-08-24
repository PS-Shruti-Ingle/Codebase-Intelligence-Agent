#!/usr/bin/env python3
"""
MCP Server — Codebase Intelligence Agent  (V1)
================================================
Exposes repository analysis capabilities via the Model Context Protocol.

Tools      : clone_repository, get_branches, get_repo_structure, read_file,
             search_code, find_references, trace_execution, check_repo_status,
             sync_repository, build_relationship_graph, build_component_graph,
             semantic_search, get_git_history, get_github_context,
             generate_documentation, validate_citation, fetch_github_repo_info

Resources  : repo://structure, repo://readme, repo://metadata, repo://git_status
Prompts    : architecture_analysis, execution_flow, code_review, module_explanation
"""

import json
import sys
import hashlib
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Dict, List

# ── ensure sibling modules are importable ────────────────────────────────────
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from code_analyzer import CodeAnalyzer, IGNORE_DIRS, LANGUAGE_EXTENSIONS
from retrieval import RetrievalEngine

# ── MCP import — supports mcp 2.x (MCPServer) and older (FastMCP) ────────────
try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "mcp", "-q"])
        from mcp.server.mcpserver import MCPServer as FastMCP

# ── gitpython auto-discovery ────────────────────────────────────────────────
import os
os.environ["GIT_PYTHON_REFRESH"] = "quiet"
if sys.platform == "win32":
    _common_git_paths = [
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd")),
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin")),
        Path(r"C:\Program Files\Git\cmd"),
        Path(r"C:\Program Files\Git\bin"),
        Path(r"C:\Program Files (x86)\Git\cmd"),
        Path(r"C:\Program Files (x86)\Git\bin"),
    ]
    for _p in _common_git_paths:
        _git_exe = _p / "git.exe"
        if _git_exe.exists():
            os.environ["GIT_PYTHON_GIT_EXECUTABLE"] = str(_git_exe)
            if str(_p) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = f"{_p};" + os.environ.get("PATH", "")
            break

try:
    import git
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "gitpython", "-q"])
    import git

# ── Init ─────────────────────────────────────────────────────────────────────
mcp = FastMCP("Codebase Intelligence Agent")

# ── Per-repo instance caches (keyed by resolved repo path string) ─────────────
_analyzer_cache: Dict[str, CodeAnalyzer]    = {}
_retrieval_cache: Dict[str, RetrievalEngine] = {}
_doc_cache: Dict[str, str]                  = {}   # cache_key -> documentation JSON
_component_graph_cache: Dict[str, dict]     = {}   # cache_key -> graph dict


def _canonical_repo_id(url: str) -> str:
    """
    Normalizes any GitHub repository URL to a canonical identifier: 'owner/repo'.
    Handles trailing slashes, .git extensions, SSH URLs, HTTPS URLs, and sub-paths.
    """
    if not url:
        return ""
    import re
    u = url.strip()
    u = re.sub(r'^(git\+https?://|git\+ssh://|git@)', 'https://', u)
    u = re.sub(r'^https?://github\.com:', 'https://github.com/', u)
    u = re.sub(r'https?://[^@]+@github\.com/', 'https://github.com/', u)
    u = u.rstrip('/')
    if u.endswith('.git'):
        u = u[:-4]
    match = re.search(r'github\.com/([^/]+)/([^/]+)', u, re.IGNORECASE)
    if match:
        owner = match.group(1).lower().strip()
        repo = match.group(2).lower().strip()
        return f"{owner}/{repo}"
    parts = [p for p in u.rstrip('/').split('/') if p]
    if len(parts) >= 2:
        return f"{parts[-2].lower().strip()}/{parts[-1].lower().strip()}"
    return u.lower().strip()


def _workspace_id_for(canonical_id: str) -> str:
    """Derive a stable, unique workspace ID from canonical repo ID."""
    if not canonical_id:
        return "ws_default"
    return f"ws_{hashlib.sha256(canonical_id.encode('utf-8')).hexdigest()[:12]}"


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


def _invalidate_all_caches():
    """Wipe all repository analyzers, retrieval indexes, doc caches, and component graphs."""
    _analyzer_cache.clear()
    _retrieval_cache.clear()
    _doc_cache.clear()
    _component_graph_cache.clear()


def _invalidate_cache(repo: Path):
    """Call after sync or re-clone so stale cached objects are dropped."""
    key = str(repo.resolve())
    _analyzer_cache.pop(key, None)
    _retrieval_cache.pop(key, None)
    # Also clear keyed caches that start with this path
    for k in list(_doc_cache.keys()):
        if k.startswith(key):
            _doc_cache.pop(k, None)
    for k in list(_component_graph_cache.keys()):
        if k.startswith(key):
            _component_graph_cache.pop(k, None)


def _repo_cache_key(repo: Path) -> str:
    """Stable cache key: repo_path + current HEAD SHA."""
    try:
        r = git.Repo(repo)
        sha = r.head.commit.hexsha[:12]
    except Exception:
        sha = "unknown"
    return f"{str(repo.resolve())}:{sha}"


# Persistent state file – survives server restarts
_STATE_FILE = _HERE.parent / "repos" / ".state.json"
_SESSION_ROOT = (_HERE.parent / "repos" / ".sessions").resolve()


def _remove_session_repo(repo: Optional[Path]) -> bool:
    if not repo:
        return False
    try:
        resolved = repo.resolve()
        if resolved == _SESSION_ROOT or _SESSION_ROOT not in resolved.parents:
            return False
        import os
        import shutil
        def _onerror(func, path, _exc):
            try:
                os.chmod(path, 0o700)
                func(path)
            except Exception:
                pass
        shutil.rmtree(resolved, onerror=_onerror)
        return not resolved.exists()
    except Exception:
        return False


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
    return {
        "repo_path": None,
        "repo_url": None,
        "canonical_id": None,
        "workspace_id": None,
        "branch": None,
        "commit_sha": None,
    }


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


def _safe_path(repo: Path, file_path: str) -> Optional[Path]:
    """Resolve and validate file_path is inside repo. Returns None if unsafe."""
    try:
        if Path(file_path).is_absolute():
            full = Path(file_path).resolve()
        else:
            full = (repo / file_path).resolve()
        if str(full).startswith(str(repo.resolve())):
            return full
    except Exception:
        pass
    return None


def _github_request(url: str, token: Optional[str] = None) -> dict:
    """Make a GitHub API request. Returns parsed JSON or raises."""
    import os
    headers = {'User-Agent': 'CodebaseIntelligenceAgent/1.0'}
    tok = token or os.environ.get("GITHUB_TOKEN", "")
    if tok:
        headers["Authorization"] = f"token {tok}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


# ===========================================================================
# TOOLS
# ===========================================================================

@mcp.tool()
def clone_repository(url: str, branch: str = "") -> str:
    """
    Clone a public GitHub repository to local storage.
    Returns JSON with status, repo_path, repo_name, canonical_id, workspace_id, resolved_branch, branches list, and is_switch.
    branch is optional — leave empty to use the repository default branch.
    """
    # Input validation
    url = url.strip()
    if not url:
        return json.dumps({
            "status": "error",
            "message": "Please provide a valid public GitHub repository URL."
        })

    target_canonical = _canonical_repo_id(url)
    if not target_canonical or ("github.com" not in url and not url.endswith(".git") and "/" not in target_canonical):
        return json.dumps({
            "status": "error",
            "message": "Please provide a valid public GitHub repository URL (e.g., https://github.com/owner/repo)"
        })

    target_workspace_id = _workspace_id_for(target_canonical)
    previous_state = _load_state()
    previous_url = previous_state.get("repo_url") or ""
    previous_canonical = _canonical_repo_id(previous_url)
    previous_repo = _current_repo()

    is_same_repo = (
        previous_canonical == target_canonical
        and previous_repo is not None
        and previous_repo.exists()
    )

    if is_same_repo:
        try:
            existing = git.Repo(previous_repo)
            if branch and branch not in ("", "main", "master", "default") and existing.active_branch.name != branch:
                try:
                    existing.git.checkout(branch)
                except Exception:
                    pass
            resolved_branch = existing.active_branch.name
            branches = _list_branches(existing)
            commit_sha = existing.head.commit.hexsha
            _save_state({
                "repo_path": str(previous_repo),
                "repo_url": url,
                "canonical_id": target_canonical,
                "workspace_id": target_workspace_id,
                "branch": resolved_branch,
                "commit_sha": commit_sha,
            })
            return json.dumps({
                "status": "already_exists",
                "is_switch": False,
                "message": f"Repository already cloned at {previous_repo}",
                "repo_path": str(previous_repo),
                "repo_name": target_canonical.split("/")[-1],
                "canonical_id": target_canonical,
                "workspace_id": target_workspace_id,
                "resolved_branch": resolved_branch,
                "branches": branches,
                "commit_sha": commit_sha[:7] if commit_sha else "",
            })
        except Exception:
            pass

    # GENUINE REPOSITORY SWITCH OR INITIAL LOAD — ATOMIC CLEANUP
    _invalidate_all_caches()

    if previous_repo:
        _remove_session_repo(previous_repo)

    if _SESSION_ROOT.exists():
        import os
        import shutil
        def _onerror(func, path, _exc):
            try:
                os.chmod(path, 0o700)
                func(path)
            except Exception:
                pass
        for child in _SESSION_ROOT.iterdir():
            if child.is_dir() and _SESSION_ROOT in child.resolve().parents:
                shutil.rmtree(child, onerror=_onerror)

    repos_dir = _SESSION_ROOT
    repos_dir.mkdir(parents=True, exist_ok=True)

    import re
    raw_name = target_canonical.split("/")[-1]
    repo_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", raw_name)[:80]
    repo_path = repos_dir / repo_name

    # Attempt clone
    clone_kwargs: dict = {}
    if branch and branch not in ("", "main", "master", "default"):
        clone_kwargs["branch"] = branch

    try:
        git.Repo.clone_from(url, repo_path, **clone_kwargs)
    except git.exc.GitCommandError as first_err:
        if "Repository not found" in str(first_err) or "not found" in str(first_err).lower():
            return json.dumps({
                "status": "error",
                "message": "Repository not found or is private. Only public repositories are supported."
            })
        # Retry without branch constraint
        try:
            if repo_path.exists():
                import shutil
                shutil.rmtree(repo_path, ignore_errors=True)
            git.Repo.clone_from(url, repo_path)
        except Exception as exc:
            if repo_path.exists():
                import shutil
                shutil.rmtree(repo_path, ignore_errors=True)
            return json.dumps({"status": "error", "message": str(exc)})
    except Exception as exc:
        if repo_path.exists():
            import shutil
            shutil.rmtree(repo_path, ignore_errors=True)
        return json.dumps({"status": "error", "message": str(exc)})

    # Resolve the actual checked-out branch name
    try:
        cloned = git.Repo(repo_path)
        resolved_branch = cloned.active_branch.name
        branches = _list_branches(cloned)
        commit_sha = cloned.head.commit.hexsha
    except Exception:
        resolved_branch = branch or "main"
        branches = [resolved_branch]
        commit_sha = ""

    _save_state({
        "repo_path": str(repo_path),
        "repo_url": url,
        "canonical_id": target_canonical,
        "workspace_id": target_workspace_id,
        "branch": resolved_branch,
        "commit_sha": commit_sha,
    })

    return json.dumps({
        "status": "success",
        "is_switch": True,
        "message": "Repository cloned successfully into fresh workspace",
        "repo_path": str(repo_path),
        "repo_name": repo_name,
        "canonical_id": target_canonical,
        "workspace_id": target_workspace_id,
        "resolved_branch": resolved_branch,
        "branches": branches,
        "commit_sha": commit_sha[:7] if commit_sha else "",
    })


@mcp.tool()
def cleanup_repository() -> str:
    """Delete the active temporary repository, clear session state, and invalidate all caches."""
    _invalidate_all_caches()
    state = _load_state()
    repo = _current_repo()
    removed = _remove_session_repo(repo)
    if _SESSION_ROOT.exists():
        import os
        import shutil
        def _onerror(func, path, _exc):
            try:
                os.chmod(path, 0o700)
                func(path)
            except Exception:
                pass
        for child in _SESSION_ROOT.iterdir():
            if child.is_dir() and _SESSION_ROOT in child.resolve().parents:
                shutil.rmtree(child, onerror=_onerror)
        removed = removed or not any(_SESSION_ROOT.iterdir())
    state.update({
        "repo_path": None,
        "repo_url": None,
        "canonical_id": None,
        "workspace_id": None,
        "branch": None,
        "commit_sha": None
    })
    _save_state(state)
    return json.dumps({"status": "cleaned", "removed": removed})


def _list_branches(repo: git.Repo) -> List[str]:
    """List local + remote branches, deduped and sorted."""
    branches = set()
    try:
        for b in repo.branches:
            branches.add(b.name)
    except Exception:
        pass
    try:
        for ref in repo.remotes.origin.refs:
            name = ref.name.replace("origin/", "")
            if name not in ("HEAD",):
                branches.add(name)
    except Exception:
        pass
    return sorted(branches) or ["main"]


@mcp.tool()
def get_branches() -> str:
    """
    Return the list of available branches in the loaded repository.
    Returns JSON with branches list and current branch.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded. Use clone_repository first."})
    try:
        r = git.Repo(repo)
        current = r.active_branch.name
        branches = _list_branches(r)
        return json.dumps({
            "current_branch": current,
            "branches": branches,
            "total": len(branches),
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


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
    Repository content is treated as untrusted data — never executed.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    full = _safe_path(repo, file_path)
    if not full:
        return json.dumps({"error": "Access denied: path is outside the repository."})

    if not full.exists():
        return json.dumps({"error": f"File not found: {file_path}"})
    if full.stat().st_size > 2 * 1024 * 1024:
        return json.dumps({"error": "File too large to display (> 2 MB)."})
    if full.is_dir():
        return json.dumps({"error": "Path is a directory, not a file."})

    # Block binary-looking files by extension
    BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff",
                   ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".tar", ".gz",
                   ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc"}
    if full.suffix.lower() in BINARY_EXTS:
        return json.dumps({"error": f"Binary file ({full.suffix}) — display not supported."})

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

    try:
        branch = r.active_branch.name
    except Exception:
        branch = "unknown"

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

    try:
        changed = [item.a_path for item in r.index.diff(None)]
    except Exception:
        changed = []

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
    """
    if not confirmed:
        return json.dumps({
            "status": "confirmation_required",
            "message": "Syncing will pull remote commits into your local copy. Call again with confirmed=True to proceed.",
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
        _invalidate_cache(repo)

        # Update commit SHA in state
        state = _load_state()
        state["commit_sha"] = r.head.commit.hexsha
        _save_state(state)

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
def build_component_graph() -> str:
    """
    Build a comprehensive component-level architecture graph of the codebase.
    Visualizes layers: UI / Frontend, Key Components, Services, State / Context,
    Backend / APIs, Database, and External AI & Cloud Integrations.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    cache_key = _repo_cache_key(repo)
    if cache_key in _component_graph_cache:
        return json.dumps(_component_graph_cache[cache_key], indent=2)

    analyzer = _get_analyzer(repo)
    metadata = analyzer.get_metadata()
    structure = analyzer.get_structure(max_depth=3)

    nodes = []
    edges = []
    node_id = [0]

    def new_id(prefix: str = "comp") -> str:
        node_id[0] += 1
        return f"{prefix}_{node_id[0]}"

    added: Dict[str, str] = {}  # label -> id

    def add_node(label: str, node_type: str, description: str = "", path: str = "") -> str:
        if label in added:
            return added[label]
        nid = new_id(node_type)
        added[label] = nid
        node_obj = {"id": nid, "label": label, "type": node_type, "description": description}
        if path:
            node_obj["path"] = path
        nodes.append(node_obj)
        return nid

    def add_edge(src: str, tgt: str, rel: str):
        if src and tgt and src != tgt:
            edges.append({"source": src, "target": tgt, "type": rel})

    # 1. Entry actor
    user_node = add_node("User / Client", "ui", "End-user interaction / Web browser")

    # 2. Main Entry Points
    entry_nodes = []
    for ep in metadata.get("entry_points", []):
        ep_name = Path(ep).name
        nid = add_node(ep_name, "ui" if ep_name.endswith((".tsx", ".jsx", ".html")) else "service", f"Main entry point ({ep})", ep)
        entry_nodes.append(nid)
        add_edge(user_node, nid, "accesses")

    # If no entry points detected, look for standard entry files
    if not entry_nodes:
        for fname in ["App.tsx", "App.jsx", "main.tsx", "index.html", "index.js", "app.py", "main.py", "server.js", "server.ts"]:
            p = repo / fname
            if p.exists():
                nid = add_node(fname, "ui" if fname.endswith((".tsx", ".jsx", ".html")) else "service", f"Entry file ({fname})", fname)
                entry_nodes.append(nid)
                add_edge(user_node, nid, "accesses")
                break

    # 3. Scan Directories & map to architecture layers
    _dir_component_map = {
        "components": ("UI Components", "ui"),
        "frontend": ("Frontend App", "ui"),
        "ui": ("UI Layer", "ui"),
        "client": ("Client Layer", "ui"),
        "web": ("Web Interface", "ui"),
        "gui": ("GUI Layer", "ui"),
        "pages": ("Page Views", "ui"),
        "views": ("Views", "ui"),
        "screens": ("Screens", "ui"),
        "public": ("Static Assets", "ui"),
        "static": ("Static Assets", "ui"),

        "hooks": ("State Hooks", "middleware"),
        "context": ("Application Context", "middleware"),
        "contexts": ("Application Context", "middleware"),
        "store": ("State Store", "middleware"),
        "stores": ("State Store", "middleware"),
        "state": ("State Management", "middleware"),

        "services": ("Service Layer", "service"),
        "service": ("Service Layer", "service"),
        "backend": ("Backend Core", "service"),
        "api": ("API Routes", "api"),
        "server": ("Server Engine", "service"),
        "app": ("Application Core", "service"),
        "src": ("Source Core", "service"),
        "core": ("Core Domain", "service"),

        "auth": ("Authentication", "auth"),
        "authentication": ("Authentication", "auth"),
        "middleware": ("Middleware", "middleware"),

        "db": ("Database Layer", "database"),
        "database": ("Database Layer", "database"),
        "models": ("Data Models", "database"),
        "model": ("Data Models", "database"),
        "schema": ("Schema Definitions", "database"),

        "routes": ("API Routes", "api"),
        "controllers": ("Controllers", "api"),
        "handlers": ("Request Handlers", "api"),

        "workers": ("Background Workers", "worker"),
        "jobs": ("Background Jobs", "worker"),
        "tasks": ("Task Queue", "worker"),
        "queue": ("Message Queue", "worker"),

        "utils": ("Utilities", "util"),
        "helpers": ("Helpers", "util"),
        "lib": ("Shared Library", "util"),
        "common": ("Common Modules", "util"),
        "shared": ("Shared Utilities", "util"),

        "tests": ("Test Suite", "test"),
        "test": ("Test Suite", "test"),
        "__tests__": ("Test Suite", "test"),
        "spec": ("Test Suite", "test"),

        "config": ("Configuration", "config"),
        "scripts": ("Build Scripts", "config"),
    }

    dir_nodes = {}
    if isinstance(structure, dict) and structure.get("type") == "directory":
        for child in structure.get("children", []):
            if child.get("type") != "directory":
                continue
            dir_name = child.get("name", "").lower()
            if dir_name in _dir_component_map:
                label, ntype = _dir_component_map[dir_name]
                dnid = add_node(label, ntype, f"Directory /{child['name']}", child.get("path", ""))
                dir_nodes[dir_name] = dnid
                for en in entry_nodes:
                    if ntype in ("ui", "middleware", "service", "api"):
                        add_edge(en, dnid, "renders/uses")

    # 4. Extract concrete individual UI Components (e.g. ChatPanel, CookbookView, CookModeView, etc.)
    comp_dir = repo / "components"
    if not comp_dir.exists():
        comp_dir = repo / "src" / "components"
    if comp_dir.exists() and comp_dir.is_dir():
        for comp_file in sorted(comp_dir.iterdir()):
            if comp_file.is_file() and comp_file.suffix in (".tsx", ".jsx", ".vue", ".js", ".ts"):
                cname = comp_file.stem
                if cname.lower() in ("index", "types", "vite-env.d"):
                    continue
                rel_path = str(comp_file.relative_to(repo)).replace("\\", "/")
                cid = add_node(cname, "ui", f"UI Component ({rel_path})", rel_path)
                if "components" in dir_nodes:
                    add_edge(dir_nodes["components"], cid, "contains")
                for en in entry_nodes:
                    add_edge(en, cid, "renders")

    # 5. Extract concrete Service modules (e.g. geminiService.ts, apiService.py, etc.)
    svc_dir = repo / "services"
    if not svc_dir.exists():
        svc_dir = repo / "src" / "services"
    if svc_dir.exists() and svc_dir.is_dir():
        for svc_file in sorted(svc_dir.iterdir()):
            if svc_file.is_file() and svc_file.suffix in (".ts", ".js", ".py", ".go", ".rs"):
                sname = svc_file.stem
                if sname.lower() in ("index", "types"):
                    continue
                rel_path = str(svc_file.relative_to(repo)).replace("\\", "/")
                sid = add_node(sname, "service", f"Service Module ({rel_path})", rel_path)
                if "services" in dir_nodes:
                    add_edge(dir_nodes["services"], sid, "contains")
                for en in entry_nodes:
                    add_edge(en, sid, "invokes")
                for n in nodes:
                    if n["type"] == "ui" and n["id"] not in (user_node, *entry_nodes):
                        add_edge(n["id"], sid, "calls")

    # 6. Detected Frameworks & Bundlers
    for fw in metadata.get("frameworks", []):
        fw_map = {
            "React": ("React Framework", "ui"),
            "Vite": ("Vite Bundler", "config"),
            "Vue.js": ("Vue.js Frontend", "ui"),
            "Angular": ("Angular Frontend", "ui"),
            "Next.js": ("Next.js Engine", "ui"),
            "Express": ("Express API Server", "service"),
            "Flask": ("Flask REST API", "api"),
            "FastAPI": ("FastAPI Server", "api"),
            "Django": ("Django Application", "service"),
            "TailwindCSS": ("Tailwind CSS", "ui"),
            "Spring": ("Spring Backend", "service"),
            "Rails": ("Rails Framework", "service"),
            "SQLAlchemy": ("SQLAlchemy ORM", "database"),
            "Pytest": ("Pytest Testing Suite", "test"),
        }
        if fw in fw_map:
            label, ntype = fw_map[fw]
            fnid = add_node(label, ntype, f"Framework: {fw}")
            for en in entry_nodes:
                add_edge(en, fnid, "built_with")

    # 7. Detect External AI, DB & Cloud Integrations
    deps_all = []
    for dep_list in metadata.get("dependencies", {}).values():
        deps_all.extend(dep_list)

    external_services = {
        "@google/genai": ("Google Gemini AI", "ai", "Google Gemini Multimodal AI Engine"),
        "google-genai": ("Google Gemini AI", "ai", "Google Gemini Multimodal AI Engine"),
        "google-generativeai": ("Google Gemini AI", "ai", "Google Gemini API"),
        "openai": ("OpenAI API", "ai", "OpenAI LLM Integration"),
        "anthropic": ("Anthropic Claude", "ai", "Anthropic Claude API"),
        "langchain": ("LangChain", "ai", "AI Orchestration Framework"),
        "groq": ("Groq LLM Engine", "ai", "Ultra-fast LPU Inference"),
        "redis": ("Redis Cache", "cache", "In-Memory Cache / Key-Value Store"),
        "firebase": ("Firebase Cloud", "external", "Firebase Authentication & Database"),
        "supabase": ("Supabase", "database", "Postgres & Auth Backend"),
        "mongodb": ("MongoDB", "database", "NoSQL Document Database"),
        "mongoose": ("Mongoose ODM", "database", "MongoDB Object Modeling"),
        "postgres": ("PostgreSQL", "database", "Relational Database"),
        "pg": ("PostgreSQL Client", "database", "PostgreSQL Database Driver"),
        "sqlite": ("SQLite Database", "database", "Embedded Database"),
        "prisma": ("Prisma ORM", "database", "Type-safe Database Client"),
        "stripe": ("Stripe Payments", "external", "Payment Processing Gateway"),
    }

    for dep in deps_all:
        dep_lower = dep.lower().split("[")[0].split(">=")[0].strip()
        for key, (label, ntype, desc) in external_services.items():
            if key in dep_lower:
                ext_id = add_node(label, ntype, desc)
                connected = False
                for n in nodes:
                    if n["type"] == "service":
                        add_edge(n["id"], ext_id, "interacts_with")
                        connected = True
                if not connected:
                    for en in entry_nodes:
                        add_edge(en, ext_id, "interacts_with")
                break

    # Deduplicate edges
    seen_edges = set()
    unique_edges = []
    for e in edges:
        k = (e["source"], e["target"], e["type"])
        if k not in seen_edges:
            seen_edges.add(k)
            unique_edges.append(e)

    result = {
        "nodes": nodes,
        "edges": unique_edges,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(unique_edges),
            "metadata": {
                "primary_language": metadata.get("primary_language", "Unknown"),
                "frameworks": metadata.get("frameworks", []),
                "total_files": metadata.get("total_files", 0),
            }
        },
        "graph_type": "component",
    }

    _component_graph_cache[cache_key] = result
    return json.dumps(result, indent=2)


@mcp.tool()
def get_component_graph() -> str:
    """Alias for build_component_graph."""
    return build_component_graph()


@mcp.tool()
def get_relationship_graph() -> str:
    """Alias for build_relationship_graph."""
    return build_relationship_graph()


@mcp.tool()
def get_repository_overview() -> str:
    """Return repository overview, description, and primary entry points."""
    doc = json.loads(generate_documentation())
    return json.dumps(doc.get("overview", {}), indent=2)


@mcp.tool()
def get_dependencies() -> str:
    """Return repository dependencies (Node, Python, etc.)."""
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})
    analyzer = _get_analyzer(repo)
    metadata = analyzer.get_metadata()
    return json.dumps(metadata.get("dependencies", {}), indent=2)


@mcp.tool()
def detect_tech_stack() -> str:
    """Detect technologies, primary language, frameworks, and statistics."""
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})
    analyzer = _get_analyzer(repo)
    metadata = analyzer.get_metadata()
    return json.dumps({
        "primary_language": metadata.get("primary_language", "Unknown"),
        "languages": metadata.get("languages", {}),
        "frameworks": metadata.get("frameworks", []),
        "total_files": metadata.get("total_files", 0),
        "total_lines": metadata.get("total_lines", 0),
    }, indent=2)


@mcp.tool()
def get_metadata() -> str:
    """Return full repository metadata."""
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})
    analyzer = _get_analyzer(repo)
    return json.dumps(analyzer.get_metadata(), indent=2)


@mcp.tool()
def analyze_code(file_path: str) -> str:
    """
    Analyze a specific file to extract structure, classes, functions, imports, and exports.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})
    analyzer = _get_analyzer(repo)
    return json.dumps(analyzer.analyze_file(file_path), indent=2)


@mcp.tool()
def lexical_search(
    query: str,
    file_pattern: str = "**/*",
    is_regex: bool = False,
    case_sensitive: bool = False,
    top_k: int = 20,
) -> str:
    """Lexical search alias for search_code."""
    return search_code(query, file_pattern, is_regex, case_sensitive)


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
def get_git_history(
    file_path: str = "",
    max_commits: int = 20,
    include_diff: bool = False,
) -> str:
    """
    Retrieve git history. If file_path is given, returns history for that file.
    Otherwise returns recent commits across the whole repo.
    Optionally include diff stats per commit.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    try:
        r = git.Repo(repo)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    try:
        kwargs: dict = {"max_count": min(max_commits, 50)}
        if file_path:
            # Validate path is inside repo
            safe = _safe_path(repo, file_path)
            if not safe:
                return json.dumps({"error": "Path is outside the repository."})
            kwargs["paths"] = file_path

        commits = []
        for c in r.iter_commits(**kwargs):
            entry: dict = {
                "sha": c.hexsha[:7],
                "full_sha": c.hexsha,
                "message": c.message.strip().split("\n")[0][:200],
                "full_message": c.message.strip()[:500],
                "author": str(c.author),
                "email": str(c.author.email) if hasattr(c.author, "email") else "",
                "date": c.committed_datetime.isoformat(),
                "files_changed": [],
            }

            # Changed files
            try:
                if c.parents:
                    diffs = c.diff(c.parents[0])
                    entry["files_changed"] = [d.a_path or d.b_path for d in diffs][:20]
                    if include_diff and len(entry["files_changed"]) <= 5:
                        diff_text = r.git.diff(c.parents[0].hexsha, c.hexsha, stat=True)
                        entry["diff_stat"] = diff_text[:1000]
            except Exception:
                pass

            commits.append(entry)

        return json.dumps({
            "file_path": file_path or None,
            "commits": commits,
            "total": len(commits),
        }, indent=2)

    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def get_github_context(owner: str = "", repo_name: str = "", query: str = "") -> str:
    """
    Fetch GitHub context: repository info, recent issues, and pull requests.
    If owner/repo_name not provided, extracts them from the loaded repository URL.
    Use query to filter issues/PRs by keyword.
    """
    # Try to infer owner/repo from state if not provided
    if not owner or not repo_name:
        state = _load_state()
        url = state.get("repo_url", "")
        try:
            import re
            m = re.match(r"https?://github\.com/([^/]+)/([^/\s]+?)(?:\.git)?/?$", url)
            if m:
                owner, repo_name = m.group(1), m.group(2)
        except Exception:
            pass

    if not owner or not repo_name:
        return json.dumps({
            "error": "Could not determine GitHub owner/repo. Please provide owner and repo_name."
        })

    result: dict = {"owner": owner, "repo": repo_name}

    # Repository info
    try:
        info = _github_request(f"https://api.github.com/repos/{owner}/{repo_name}")
        result["repository"] = {
            "description": info.get("description"),
            "stars": info.get("stargazers_count"),
            "forks": info.get("forks_count"),
            "open_issues": info.get("open_issues_count"),
            "default_branch": info.get("default_branch"),
            "language": info.get("language"),
            "topics": info.get("topics", []),
            "created_at": info.get("created_at"),
            "updated_at": info.get("updated_at"),
        }
    except urllib.error.HTTPError as e:
        result["repository_error"] = f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        result["repository_error"] = str(e)

    # Recent issues
    try:
        issues_url = f"https://api.github.com/repos/{owner}/{repo_name}/issues?state=open&per_page=10&sort=updated"
        issues_data = _github_request(issues_url)
        issues = []
        for iss in issues_data:
            if iss.get("pull_request"):
                continue  # skip PRs from issues endpoint
            body = iss.get("body") or ""
            if query and query.lower() not in (iss.get("title", "") + body).lower():
                continue
            issues.append({
                "number": iss.get("number"),
                "title": iss.get("title"),
                "state": iss.get("state"),
                "created_at": iss.get("created_at"),
                "labels": [l.get("name") for l in iss.get("labels", [])],
                "body_preview": body[:300] + ("..." if len(body) > 300 else ""),
                "url": iss.get("html_url"),
            })
        result["recent_issues"] = issues[:8]
    except urllib.error.HTTPError:
        result["issues_error"] = "Could not fetch issues (rate limit or permissions)"
    except Exception as e:
        result["issues_error"] = str(e)

    # Recent PRs
    try:
        prs_url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls?state=closed&per_page=10&sort=updated"
        prs_data = _github_request(prs_url)
        prs = []
        for pr in prs_data:
            body = pr.get("body") or ""
            if query and query.lower() not in (pr.get("title", "") + body).lower():
                continue
            prs.append({
                "number": pr.get("number"),
                "title": pr.get("title"),
                "state": pr.get("state"),
                "merged": pr.get("merged_at") is not None,
                "merged_at": pr.get("merged_at"),
                "author": pr.get("user", {}).get("login"),
                "body_preview": body[:300] + ("..." if len(body) > 300 else ""),
                "url": pr.get("html_url"),
            })
        result["recent_prs"] = prs[:8]
    except urllib.error.HTTPError:
        result["prs_error"] = "Could not fetch pull requests"
    except Exception as e:
        result["prs_error"] = str(e)

    return json.dumps(result, indent=2)


@mcp.tool()
def generate_documentation() -> str:
    """
    Generate structured project documentation by analyzing the repository.
    Produces: project overview, goal, tech stack, major components,
    implementation summary, data flow, setup instructions, and suggested questions.
    Uses real repository evidence — does NOT invent project facts.
    Results are cached by commit SHA.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    cache_key = _repo_cache_key(repo)
    if cache_key in _doc_cache:
        return _doc_cache[cache_key]

    analyzer = _get_analyzer(repo)
    metadata = analyzer.get_metadata()
    state = _load_state()

    # Gather evidence from repo
    evidence = {}

    # README
    readme_text = ""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        f = repo / name
        if f.exists():
            readme_text = f.read_text(encoding="utf-8", errors="replace")[:3000]
            evidence["readme_file"] = name
            break

    # Package files
    pkg_evidence = {}
    pkg = repo / "package.json"
    if pkg.exists():
        try:
            pkg_data = json.loads(pkg.read_text())
            pkg_evidence["name"] = pkg_data.get("name", "")
            pkg_evidence["description"] = pkg_data.get("description", "")
            pkg_evidence["scripts"] = list(pkg_data.get("scripts", {}).keys())
            pkg_evidence["main_deps"] = list(pkg_data.get("dependencies", {}).keys())[:10]
        except Exception:
            pass

    setup_py = repo / "setup.py"
    pyproject = repo / "pyproject.toml"
    if setup_py.exists():
        pkg_evidence["setup_py"] = setup_py.read_text(encoding="utf-8", errors="replace")[:500]
    elif pyproject.exists():
        pkg_evidence["pyproject"] = pyproject.read_text(encoding="utf-8", errors="replace")[:500]

    # Docker / infra files
    infra_files = []
    for f in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".env.example",
              "Makefile", "kubernetes", ".github/workflows"):
        if (repo / f).exists():
            infra_files.append(f)

    # Key entry points — read first 40 lines of each
    entry_point_snippets = {}
    for ep in metadata.get("entry_points", [])[:3]:
        ep_path = repo / ep
        if ep_path.exists():
            try:
                lines = ep_path.read_text(encoding="utf-8", errors="replace").splitlines()[:40]
                entry_point_snippets[ep] = "\n".join(lines)
            except Exception:
                pass

    # Generate suggested questions based on what we found
    suggested_questions = [
        "What does this project do?",
        "What is the main entry point?",
        "How does data flow through the application?",
        "What are the main components?",
        "What external services are used?",
    ]

    fw = metadata.get("frameworks", [])
    if any(f in fw for f in ["Flask", "FastAPI", "Django", "Express"]):
        suggested_questions.append("How are API routes defined?")
    if any(f in fw for f in ["React", "Vue.js", "Angular", "Next.js"]):
        suggested_questions.append("How does the frontend communicate with the backend?")
    if any(f in fw for f in ["SQLAlchemy", "Sequelize", "Prisma"]):
        suggested_questions.append("Where is the database accessed?")
    if "JWT" in str(metadata) or "auth" in str(metadata).lower():
        suggested_questions.append("How is authentication implemented?")
    suggested_questions.append("What changed recently in the codebase?")
    suggested_questions.append("Where should I start to understand this project?")

    # Build structured documentation from real evidence
    doc = {
        "project_name": (
            pkg_evidence.get("name")
            or state.get("repo_url", "").rstrip("/").split("/")[-1]
            or "Unknown Project"
        ),
        "repository_url": state.get("repo_url", ""),
        "commit_sha": state.get("commit_sha", "")[:7],
        "branch": state.get("branch", "main"),

        "overview": {
            "description": pkg_evidence.get("description") or (readme_text[:300] if readme_text else "No description found."),
            "readme_available": bool(readme_text),
            "readme_file": evidence.get("readme_file"),
            "readme_preview": readme_text[:1500] if readme_text else None,
        },

        "tech_stack": {
            "primary_language": metadata.get("primary_language", "Unknown"),
            "languages": metadata.get("languages", {}),
            "frameworks": metadata.get("frameworks", []),
            "total_files": metadata.get("total_files", 0),
            "total_lines": metadata.get("total_lines", 0),
        },

        "components": {
            "entry_points": metadata.get("entry_points", []),
            "key_config_files": metadata.get("key_config_files", []),
            "infrastructure": infra_files,
            "entry_point_previews": entry_point_snippets,
        },

        "dependencies": metadata.get("dependencies", {}),

        "setup": {
            "available_scripts": pkg_evidence.get("scripts", []),
            "docker_available": "Dockerfile" in infra_files,
            "ci_available": ".github/workflows" in infra_files,
        },

        "analysis_stats": {
            "files_analyzed": metadata.get("total_files", 0),
            "readme_found": bool(readme_text),
            "package_metadata_found": bool(pkg_evidence),
        },

        "suggested_questions": suggested_questions[:8],
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }

    result = json.dumps(doc, indent=2)
    _doc_cache[cache_key] = result
    return result


@mcp.tool()
def validate_citation(file_path: str, start_line: int, end_line: int = 0) -> str:
    """
    Validate that a file citation is real and the line range exists.
    Returns whether the citation is valid, how many lines the file has,
    and the actual content at the cited lines (for verification).
    This is the citation hallucination prevention tool.
    """
    repo = _current_repo()
    if not repo:
        return json.dumps({"error": "No repository loaded."})

    safe = _safe_path(repo, file_path)
    if not safe:
        return json.dumps({
            "valid": False,
            "reason": "Path traversal detected or path is outside repository.",
            "file_path": file_path,
        })

    if not safe.exists():
        return json.dumps({
            "valid": False,
            "reason": "File does not exist in repository.",
            "file_path": file_path,
        })

    try:
        all_lines = safe.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(all_lines)
        end = end_line if end_line > 0 else start_line

        line_valid = 1 <= start_line <= total and 1 <= end <= total and start_line <= end

        cited_content = ""
        if line_valid:
            snippet = all_lines[start_line - 1 : end]
            cited_content = "\n".join(snippet)[:500]

        return json.dumps({
            "valid": line_valid,
            "file_path": file_path,
            "total_lines": total,
            "cited_range": {"start": start_line, "end": end},
            "line_range_valid": line_valid,
            "reason": None if line_valid else f"Line range {start_line}-{end} is outside file ({total} lines total).",
            "cited_content_preview": cited_content if line_valid else None,
        })
    except Exception as exc:
        return json.dumps({
            "valid": False,
            "reason": str(exc),
            "file_path": file_path,
        })


@mcp.tool()
def fetch_github_repo_info(owner: str, repo: str) -> str:
    """
    Fetch basic repository metadata (stars, forks, open issues) from the public GitHub API.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        data = _github_request(url)
        return json.dumps({
            "success": True,
            "owner": owner,
            "repo": repo,
            "stars": data.get("stargazers_count"),
            "forks": data.get("forks_count"),
            "open_issues": data.get("open_issues_count"),
            "description": data.get("description"),
            "default_branch": data.get("default_branch"),
            "language": data.get("language"),
        }, indent=2)
    except urllib.error.HTTPError as e:
        error_type = "not_found" if e.code == 404 else "rate_limit" if e.code == 403 else "http_error"
        return json.dumps({"success": False, "error_type": error_type, "code": e.code, "message": e.reason})
    except urllib.error.URLError as e:
        return json.dumps({"success": False, "error_type": "network_error", "message": str(e.reason)})
    except Exception as e:
        return json.dumps({"success": False, "error_type": "unknown_error", "message": str(e)})


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
    """Prompt for high-level architecture analysis of the codebase."""
    return f"""You are analyzing the architecture of a codebase. Focus area: **{focus}**.

Steps:
1. Call `generate_documentation` to get a structured overview.
2. Call `build_component_graph` to understand the major components.
3. Call `get_repo_structure` to see the directory layout.
4. Read key entry-point files with `read_file`.
5. Use `search_code` to find framework-specific patterns (routes, models, services).

Deliverable:
- Overall design pattern (MVC, layered, microservices, etc.)
- Main components and their responsibilities
- Data flow and key request/response lifecycle
- External dependencies and integrations
- File references for every claim: [path:line]"""


@mcp.prompt()
def execution_flow(start_function: str, context: str = "") -> str:
    """Prompt for tracing a complete execution path through the code."""
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
    """Prompt for reviewing a specific file."""
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
    """Prompt for explaining what a module or file does."""
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

# 🧠 Codebase Intelligence Agent

An **MCP-powered AI workspace** for exploring and understanding any public GitHub repository. Combines a Python MCP server, Node.js API, and a rich browser GUI into a single developer tool.

## Architecture

```
GUI (browser)
  └─► Express API (Node.js)
        ├─► MCP Client  ──►  Python MCP Server
        │                       ├─► Tools (11): clone_repository, search_code, read_file, get_repo_structure,
        │                       │             find_references, trace_execution,
        │                       │             check_repo_status, sync_repository,
        │                       │             build_relationship_graph, semantic_search,
        │                       │             fetch_github_repo_info
        │                       ├─► Resources: repo://structure, repo://readme,
        │                       │             repo://metadata, repo://git_status
        │                       └─► Prompts:  architecture_analysis, execution_flow,
        │                                     code_review, module_explanation
        └─► Groq Llama (ReAct Q&A loop + One-Shot Reflection)
```

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.10+ |
| Node.js | 18+ |
| Git | any recent |
| Groq API Key | Required for AI Q&A |

## Quick Start (Windows)

```bat
cd CodebaseQ&A_Agent
start.bat
```

The launcher will:
1. Check Python / Node.js / Git
2. Install all dependencies automatically
3. Start the server at `http://localhost:3000`

## Manual Setup

```bash
# Python dependencies
pip install -r server/requirements.txt

# Node dependencies
cd api
npm install

# Configure API key
cp .env.example .env
# Edit .env: set GROQ_API_KEY=gsk-...

# Start
node server.js
```

## 4-Layer Retrieval

| Layer | Method | When Used |
|-------|--------|-----------|
| 1 — Lexical | Regex grep across all files | Always first |
| 2 — Structural | Python AST symbol resolution | When lexical ≤4 results |
| 3 — Semantic | TF-IDF cosine similarity | When structural still sparse |
| 4 — Agentic | Claude orchestrates all tools | Always wraps the above |

## GUI Panels

- **Left**: File explorer with collapsible tree, language icons, framework tags
- **Center**: Read-only code viewer with line numbers and syntax highlighting
- **Right/Chat**: AI Q&A with markdown rendering and clickable `[file:line]` references
- **Right/Graph**: D3.js force-directed relationship graph (files → classes → functions)

## Supported Languages

Full AST analysis: **Python**
Regex-based analysis: **JavaScript, TypeScript, JSX, TSX**
Structure + search: all text-based files

## Notes

- Only **public** GitHub repositories are supported in this version
- Repos are cloned into `repos/` inside the project directory
- The AI never hallucates file paths — every answer is grounded in actual code
- `sync_repository` always requires explicit developer confirmation

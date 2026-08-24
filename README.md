# 🧠 Codebase Intelligence Agent

> **An MCP-powered AI workspace for understanding real software repositories — with targeted retrieval, repository-grounded reasoning, and efficient LLM context usage.**

[![MCP](https://img.shields.io/badge/MCP-Powered-green)](#-mcp-is-the-hero)
[![LLM](https://img.shields.io/badge/LLM-Gemini-purple)](#-architecture)
[![Repository](https://img.shields.io/badge/Repository-Aware-blue)](#-architecture)
[![Python](https://img.shields.io/badge/Python-3.10%2B-yellow)](#-getting-started)
[![Node.js](https://img.shields.io/badge/Node.js-18%2B-brightgreen)](#-getting-started)

Codebase Intelligence Agent combines a **Python MCP server, Node.js API, and browser-based developer workspace** to let an LLM explore and reason about a GitHub repository using actual repository context.

The core idea is simple:

```text
Normal LLM
Question → Large Context → LLM → Answer

Codebase Intelligence
Question → MCP → Relevant Code → LLM → Answer
```

Instead of continuously pushing large amounts of repository content into the LLM, **MCP provides a structured interface for retrieving only the information required for the current question.**

---

## Contents

* [The Problem](#-the-problem)
* [Why Retrieval Matters](#-why-retrieval-matters)
* [MCP Is the Hero](#-mcp-is-the-hero)
* [Token Efficiency](#-token-efficiency)
* [Architecture](#-architecture)
* [MCP Capabilities](#-mcp-capabilities)
* [4-Layer Retrieval](#-4-layer-retrieval)
* [Developer Workflow](#-developer-workflow)
* [GUI](#-gui)
* [Supported Languages](#-supported-languages)
* [Evaluation](#-evaluation)
* [Getting Started](#-getting-started)

---

# ⚡ The Problem

An LLM can explain code extremely well — **if it has the right code**.

The challenge with an unfamiliar repository is that the information required to answer one question may be distributed across multiple files.

Sending large portions of the repository to the LLM repeatedly can lead to:

* unnecessary token consumption
* larger context windows
* irrelevant information
* slower reasoning
* increased inference cost

The problem is therefore not only:

> **Can the LLM understand the code?**

It is:

> **Can we efficiently give the LLM the right code to understand?**

---

# 🔎 Why Retrieval Matters

A repository may contain hundreds of files, but a question usually requires only a small subset.

```text
Entire Repository
       │
       ▼
 ┌─────────────┐
 │   Retrieval │
 └──────┬──────┘
        │
        ▼
Relevant Files / Symbols / Code
        │
        ▼
       LLM
```

The system therefore follows:

> **Retrieve → Contextualize → Reason**

rather than:

> **Dump → Process → Hope**

---

# 🧩 MCP Is the Hero

**Model Context Protocol (MCP)** is the central architectural layer of the project.

MCP acts as the bridge between the LLM and the repository.

```text
                         ┌───────────────┐
                         │      LLM      │
                         └───────┬───────┘
                                 │
                                MCP
                                 │
                                 ▼
                    ┌───────────────────────┐
                    │ Codebase Intelligence │
                    │                       │
                    │ Search • Read • Trace │
                    │ Structure • Graph     │
                    └───────────┬───────────┘
                                │
                                ▼
                         ┌─────────────┐
                         │ Repository  │
                         └─────────────┘
```

Instead of making the LLM hold the entire repository in context, MCP allows it to **interact with repository intelligence through structured tools, resources, and prompts**.

This is what makes MCP central to the architecture rather than simply another component.

---

# 🪙 Token Efficiency

Token efficiency is one of the key motivations behind the architecture.

### Conventional repository interaction

```text
Repository
     ↓
Large amount of code
     ↓
LLM Context
     ↓
Reasoning
```

As more code and conversation history are included, the amount of context processed by the model can grow rapidly.

### Codebase Intelligence

```text
User Question
      ↓
     MCP
      ↓
Retrieve relevant repository information
      ↓
Focused context
      ↓
     LLM
      ↓
Answer
```

|                        | Normal LLM + Large Repository Context | Codebase Intelligence + MCP        |
| ---------------------- | ------------------------------------- | ---------------------------------- |
| Repository access      | Context-heavy                         | Tool-driven                        |
| Code supplied to LLM   | Potentially large                     | Targeted                           |
| Irrelevant context     | Higher                                | Reduced                            |
| Token efficiency       | Lower                                 | **Designed for higher efficiency** |
| Repository exploration | Manual / context-heavy                | MCP-assisted                       |
| Context selection      | Often external/manual                 | Retrieval-driven                   |

### The key principle

```text
More unnecessary code
        ↓
More context
        ↓
More tokens
        ↓
More cost / context pressure
```

versus:

```text
Question
   ↓
MCP
   ↓
Relevant code
   ↓
Focused LLM context
```

> **MCP does not magically reduce tokens by itself. Its value is that it enables targeted repository interaction, reducing the need to repeatedly place unnecessary repository content inside the LLM context.**

---

# 🏗️ Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                         BROWSER GUI                          │
│                                                             │
│   File Explorer     Code Viewer       AI Q&A      Graph    │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                     EXPRESS API                             │
│                       Node.js                               │
└───────────────┬─────────────────────────────┬───────────────┘
                │                             │
                │ MCP Client                  │
                ▼                             ▼
┌───────────────────────────────┐      ┌──────────────────────┐
│       PYTHON MCP SERVER       │      │       GEMINI         │
│                               │      │                      │
│ Tools • Resources • Prompts   │◄────►│ ReAct Q&A +          │
│ Repository Intelligence       │      │ One-Shot Reflection  │
└───────────────┬───────────────┘      └──────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────┐
│                       REPOSITORY                            │
│                                                             │
│ Files • Code • AST • Structure • References • Git Status   │
└─────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer          | Technology           | Responsibility                        |
| -------------- | -------------------- | ------------------------------------- |
| **GUI**        | Browser UI           | Developer workspace                   |
| **API**        | Node.js + Express    | Application orchestration             |
| **MCP Client** | Node.js              | Connects application to MCP server    |
| **MCP Server** | Python               | Repository intelligence               |
| **LLM**        | Gemini               | Reasoning and response generation     |
| **Retrieval**  | Regex + AST + TF-IDF | Multi-stage code retrieval            |
| **Graph**      | D3.js                | Repository relationship visualization |
| **Repository** | Git / GitHub         | Source code                           |

---

# 🛠️ MCP Capabilities

The MCP server exposes **11 repository intelligence tools**:

| Tool                       | Purpose                             |
| -------------------------- | ----------------------------------- |
| `clone_repository`         | Clone a public GitHub repository    |
| `search_code`              | Search repository source code       |
| `read_file`                | Read repository files               |
| `get_repo_structure`       | Inspect repository structure        |
| `find_references`          | Find references to symbols/code     |
| `trace_execution`          | Trace execution paths               |
| `check_repo_status`        | Inspect repository state            |
| `sync_repository`          | Synchronize repository changes      |
| `build_relationship_graph` | Build file/symbol relationships     |
| `semantic_search`          | Retrieve semantically relevant code |
| `fetch_github_repo_info`   | Retrieve GitHub repository metadata |

### MCP Resources

The server also exposes repository resources:

```text
repo://structure
repo://readme
repo://metadata
repo://git_status
```

### MCP Prompts

Reusable repository-analysis prompts include:

```text
architecture_analysis
execution_flow
code_review
module_explanation
```

This gives the LLM access to more than simple file search — it provides a structured **repository intelligence interface**.

---

# 🔍 4-Layer Retrieval

The retrieval system combines multiple approaches rather than depending on a single search method.

| Layer              | Method                   | Role                                            |
| ------------------ | ------------------------ | ----------------------------------------------- |
| **1 — Lexical**    | Regex search             | Fast first-pass code search                     |
| **2 — Structural** | Python AST               | Symbol and structural resolution                |
| **3 — Semantic**   | TF-IDF cosine similarity | Meaning-based retrieval when results are sparse |
| **4 — Agentic**    | LLM orchestration        | Coordinates tools and reasoning                 |

```text
                    User Question
                         │
                         ▼
                ┌────────────────┐
                │  Lexical Search│
                └───────┬────────┘
                        │
                 ≤ 4 results?
                    /       \
                  Yes        No
                   │          │
                   ▼          ▼
             Structural      Context
                 AST         sufficient
                   │
                   ▼
             Semantic Search
                   │
                   ▼
             Agentic Layer
                   │
                   ▼
              Final Answer
```

This layered approach allows inexpensive retrieval methods to be used before more contextual reasoning is required.

---

# 👨‍💻 Developer Workflow

The complete workflow is built directly around the MCP architecture:

```text
        ┌────────────────────┐
        │ 1. Clone Repository│
        └─────────┬──────────┘
                  ▼
        ┌────────────────────┐
        │ 2. Open Workspace  │
        └─────────┬──────────┘
                  ▼
        ┌────────────────────┐
        │ 3. Ask Question    │
        └─────────┬──────────┘
                  ▼
        ┌────────────────────┐
        │ 4. LLM Determines   │
        │    Required Context │
        └─────────┬──────────┘
                  ▼
        ┌────────────────────┐
        │ 5. MCP Tool Call   │
        └─────────┬──────────┘
                  ▼
        ┌────────────────────┐
        │ 6. Retrieve Actual │
        │    Repository Code │
        └─────────┬──────────┘
                  ▼
        ┌────────────────────┐
        │ 7. LLM Reasoning   │
        └─────────┬──────────┘
                  ▼
        ┌────────────────────┐
        │ 8. Grounded Answer │
        └────────────────────┘
```

The developer interacts with the repository through a single workspace while MCP handles the underlying repository intelligence.

---

# 🌐 Supported Languages

| Language                   | Analysis             |
| -------------------------- | -------------------- |
| **Python**                 | Full AST analysis    |
| **JavaScript**             | Regex-based analysis |
| **TypeScript**             | Regex-based analysis |
| **JSX / TSX**              | Regex-based analysis |
| **Other text-based files** | Structure + search   |

Python receives deeper structural analysis through the AST layer, while other supported languages use repository search and structural techniques appropriate to the current implementation.

---

# 🎯 Repository Grounding

The system is designed around a strict principle:

> **Repository-specific answers should come from the repository.**

The agent does not need to invent file paths or assume that a particular implementation exists.

Repository-aware tools provide the actual source context used for reasoning.

This allows the system to distinguish between:

```text
"What normally happens in an application?"
```

and:

```text
"What actually happens in THIS repository?"
```

---

# 📊 Evaluation

The system is evaluated against a normal LLM repository-search baseline.

Key metrics include:

| Metric                         | Measures                                 |
| ------------------------------ | ---------------------------------------- |
| **Answer Correctness**         | Accuracy of the final response           |
| **Retrieval Relevance**        | Relevance of retrieved code              |
| **Retrieval Recall**           | Whether required information was found   |
| **Groundedness**               | Support from actual repository content   |
| **Hallucination Rate**         | Unsupported repository-specific claims   |
| **Token / Context Efficiency** | Unnecessary context consumed             |
| **Cross-File Reasoning**       | Ability to connect repository components |
| **Latency**                    | End-to-end response time                 |

---

# 🚀 Getting Started

## Prerequisites

| Tool         | Version            |
| ------------ | ------------------ |
| Python       | 3.10+              |
| Node.js      | 18+                |
| Git          | Any recent version |
| Gemini API Key | Required           |

---

## Windows Quick Start

```bat
cd CodebaseQ&A_Agent
start.bat
```

The launcher automatically:

1. Checks Python, Node.js, and Git
2. Installs required dependencies
3. Starts the application
4. Opens the server at:

```text
http://localhost:3000
```

---

## Manual Setup

### Python

```bash
pip install -r server/requirements.txt
```

### Node.js

```bash
cd api
npm install
```

### Environment

```bash
cp .env.example .env
```

Set:

```env
GEMINI_API_KEY=...
```

### Start

```bash
node server.js
```

---

# 📌 Notes

* Currently supports **public GitHub repositories**.
* Repositories are cloned into `repos/`.
* Repository operations are performed through the MCP layer.
* `sync_repository` requires explicit developer confirmation.
* API keys should never be committed to the repository.

---

# 🧠 Codebase Intelligence Agent

```text
Question
   ↓
MCP
   ↓
Relevant Repository Context
   ↓
LLM
   ↓
Grounded Answer
```

**The goal is not to give the LLM more code.
It is to give the LLM the right code.**

---

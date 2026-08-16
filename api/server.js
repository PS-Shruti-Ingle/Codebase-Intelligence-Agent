/**
 * Codebase Intelligence Agent — API Server
 * ==========================================
 * Express server that:
 *   1. Spawns the Python MCP server as a subprocess
 *   2. Connects an MCP client to it via stdio
 *   3. Exposes REST endpoints consumed by the GUI
 *   4. Runs an agentic Claude loop for Q&A — Claude picks and calls
 *      MCP tools iteratively until it has sufficient evidence to answer
 *
 * Architecture:
 *   GUI  →  Express API  →  MCP Client  →  Python MCP Server
 *                       →  Anthropic SDK (Claude)
 */

import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import Anthropic from '@anthropic-ai/sdk';

const __filename = fileURLToPath(import.meta.url);
const __dirname  = dirname(__filename);

const app  = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json({ limit: '10mb' }));
app.use(express.static(join(__dirname, '../gui')));

// ────────────────────────────────────────────────────────────────────────────
// Global state
// ────────────────────────────────────────────────────────────────────────────

/** @type {Client|null} */
let mcpClient = null;

/** @type {Anthropic|null} */
let anthropic = null;

/**
 * MCP tools converted to Anthropic tool format.
 * @type {Array<{name:string, description:string, input_schema:object}>}
 */
let mcpTools = [];

/**
 * MCP prompts cached at startup.
 * @type {Array<{name:string, description:string, arguments:Array}>}
 */
let mcpPrompts = [];

// Rate-limit for /api/status (git fetch is slow — cap to once per 30 s)
let _lastStatusFetch = 0;
let _cachedStatus = null;
const STATUS_INTERVAL_MS = 30_000;

// ────────────────────────────────────────────────────────────────────────────
// MCP initialisation
// ────────────────────────────────────────────────────────────────────────────

async function initMCP() {
  console.log('[MCP] Initialising…');

  // Anthropic client
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (apiKey) {
    anthropic = new Anthropic({ apiKey });
    console.log('[AI] Anthropic client ready.');
  } else {
    console.warn('[AI] ANTHROPIC_API_KEY not set — AI Q&A will be unavailable.');
  }

  // Spawn the Python MCP server
  const mcpServerPath = join(__dirname, '../server/mcp_server.py');
  const transport = new StdioClientTransport({
    command: 'python',
    args: [mcpServerPath],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  });

  mcpClient = new Client(
    { name: 'codebase-intelligence', version: '1.0.0' },
    { capabilities: {} }
  );

  await mcpClient.connect(transport);
  console.log('[MCP] Client connected to Python server.');

  // Load tools once — re-used by every request
  const { tools } = await mcpClient.listTools();
  mcpTools = tools.map(t => ({
    name: t.name,
    description: t.description ?? '',
    input_schema: t.inputSchema ?? { type: 'object', properties: {} },
  }));

  console.log(`[MCP] Loaded ${mcpTools.length} tools: ${mcpTools.map(t => t.name).join(', ')}`);

  // Load prompts once — used by /api/prompts and /api/prompt
  try {
    const { prompts } = await mcpClient.listPrompts();
    mcpPrompts = prompts.map(p => ({
      name: p.name,
      description: p.description ?? '',
      arguments: p.arguments ?? [],
    }));
    console.log(`[MCP] Loaded ${mcpPrompts.length} prompts: ${mcpPrompts.map(p => p.name).join(', ')}`);
  } catch (err) {
    console.warn('[MCP] listPrompts failed (non-fatal):', err.message);
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Agentic Q&A loop (Layer 4 — Agentic Retrieval)
// ────────────────────────────────────────────────────────────────────────────

const SYSTEM_PROMPT = `\
You are an expert Codebase Intelligence Agent with direct access to a cloned repository.
You have MCP tools that let you search, read, and analyse any file in the repo.

Rules:
1. ALWAYS use tools to gather real evidence BEFORE answering. Never answer from memory.
2. Start with search_code or find_references to locate relevant code.
3. Use read_file to inspect actual implementations — cite file paths and line numbers.
4. Use trace_execution to understand call chains when asked about flow or behaviour.
5. Combine multiple tools if needed. The quality of your answer depends on real evidence.
6. If you cannot find evidence after exhausting tools, say so honestly — do NOT guess.
7. Format code references as [path/to/file.py:42] or [path/to/file.py:10-25].
8. Write concise, developer-focused answers with code snippets where helpful.`;

/**
 * Run an agentic Claude loop: Claude calls MCP tools until it can answer.
 * @param {string} question
 * @returns {Promise<string>} Final text answer
 */
async function agenticAnswer(question) {
  if (!anthropic) throw new Error('ANTHROPIC_API_KEY is not configured.');
  if (!mcpClient) throw new Error('MCP client not connected.');

  const messages = [{ role: 'user', content: question }];
  const MAX_ITERS = 12;

  for (let iter = 0; iter < MAX_ITERS; iter++) {
    const response = await anthropic.messages.create({
      model: 'claude-sonnet-4-5',
      max_tokens: 8192,
      system: SYSTEM_PROMPT,
      tools: mcpTools,
      messages,
    });

    if (response.stop_reason === 'end_turn') {
      return response.content
        .filter(b => b.type === 'text')
        .map(b => b.text)
        .join('\n');
    }

    if (response.stop_reason === 'tool_use') {
      const toolUses = response.content.filter(b => b.type === 'tool_use');
      messages.push({ role: 'assistant', content: response.content });

      const toolResults = await Promise.all(
        toolUses.map(async tu => {
          let content;
          try {
            const result = await mcpClient.callTool({ name: tu.name, arguments: tu.input });
            content = extractText(result);
          } catch (err) {
            content = JSON.stringify({ error: err.message });
          }
          return { type: 'tool_result', tool_use_id: tu.id, content };
        })
      );

      messages.push({ role: 'user', content: toolResults });
      continue;
    }

    // Unexpected stop — return whatever text we have
    break;
  }

  return 'Analysis complete — maximum tool-call depth reached.';
}

// ────────────────────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────────────────────

function extractText(mcpResult) {
  if (!mcpResult?.content) return JSON.stringify(mcpResult ?? {});
  const textItem = mcpResult.content.find(i => i.type === 'text');
  return textItem?.text ?? JSON.stringify(mcpResult.content);
}

async function callTool(name, args = {}) {
  if (!mcpClient) throw new Error('MCP client not ready.');
  const result = await mcpClient.callTool({ name, arguments: args });
  return JSON.parse(extractText(result));
}

// ────────────────────────────────────────────────────────────────────────────
// REST Endpoints
// ────────────────────────────────────────────────────────────────────────────

// Health
app.get('/api/health', (_req, res) => {
  res.json({
    status: 'ok',
    mcpConnected: !!mcpClient,
    aiEnabled: !!anthropic,
    toolCount: mcpTools.length,
    promptCount: mcpPrompts.length,
    timestamp: new Date().toISOString(),
  });
});

// Configure API key at runtime (if user provides it in the UI)
app.post('/api/config', (req, res) => {
  const { apiKey } = req.body ?? {};
  if (!apiKey) return res.status(400).json({ error: 'apiKey required' });
  anthropic = new Anthropic({ apiKey });
  res.json({ status: 'ok', message: 'Anthropic API key accepted.' });
});

// Clone repository
app.post('/api/clone', async (req, res) => {
  const { url, branch = 'main' } = req.body ?? {};
  if (!url) return res.status(400).json({ error: 'url required' });
  try {
    const result = await callTool('clone_repository', { url, branch });
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Repository file tree
app.get('/api/structure', async (_req, res) => {
  try {
    const result = await callTool('get_repo_structure', { max_depth: 6 });
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Read a single file
app.get('/api/file', async (req, res) => {
  const { path: filePath, start, end } = req.query;
  if (!filePath) return res.status(400).json({ error: 'path query param required' });
  const args = { file_path: filePath };
  if (start) args.start_line = parseInt(start, 10);
  if (end)   args.end_line   = parseInt(end,   10);
  try {
    const result = await callTool('read_file', args);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Relationship graph (for D3.js)
app.get('/api/graph', async (_req, res) => {
  try {
    const result = await callTool('build_relationship_graph', {});
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Git status — rate-limited to 1 fetch per 30 s to avoid hammering GitHub
app.get('/api/status', async (_req, res) => {
  const now = Date.now();
  if (_cachedStatus && now - _lastStatusFetch < STATUS_INTERVAL_MS) {
    return res.json(_cachedStatus);
  }
  try {
    const result = await callTool('check_repo_status', {});
    _cachedStatus = result;
    _lastStatusFetch = now;
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Sync (pull)
app.post('/api/sync', async (req, res) => {
  const { confirmed = false } = req.body ?? {};
  try {
    const result = await callTool('sync_repository', { confirmed });
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Lexical code search
app.get('/api/search', async (req, res) => {
  const { q, pattern = '**/*', regex = 'false' } = req.query;
  if (!q) return res.status(400).json({ error: 'q query param required' });
  try {
    const result = await callTool('search_code', {
      query: q,
      file_pattern: pattern,
      is_regex: regex === 'true',
    });
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Repo metadata (languages, frameworks, deps)
app.get('/api/metadata', async (_req, res) => {
  try {
    if (!mcpClient) return res.status(503).json({ error: 'MCP not ready' });
    const result = await mcpClient.readResource({ uri: 'repo://metadata' });
    const text = result?.contents?.[0]?.text ?? '{}';
    res.json(JSON.parse(text));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// AI Q&A  ← the main agentic endpoint
app.post('/api/question', async (req, res) => {
  const { question } = req.body ?? {};
  if (!question) return res.status(400).json({ error: 'question required' });
  try {
    const answer = await agenticAnswer(question);
    res.json({ answer, timestamp: new Date().toISOString() });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// List MCP prompt templates
app.get('/api/prompts', (_req, res) => {
  res.json({ prompts: mcpPrompts });
});

// Run a named MCP prompt then answer agentically
app.post('/api/prompt', async (req, res) => {
  const { name, args = {} } = req.body ?? {};
  if (!name) return res.status(400).json({ error: 'name required' });
  if (!mcpClient) return res.status(503).json({ error: 'MCP not ready' });

  try {
    // 1. Resolve the prompt template via MCP
    const promptResult = await mcpClient.getPrompt({ name, arguments: args });

    // 2. Extract the rendered text from MCP messages list
    let promptText = '';
    for (const msg of promptResult?.messages ?? []) {
      if (typeof msg.content === 'string') {
        promptText += msg.content + '\n';
      } else if (msg.content?.text) {
        promptText += msg.content.text + '\n';
      } else if (Array.isArray(msg.content)) {
        for (const block of msg.content) {
          if (block.type === 'text') promptText += block.text + '\n';
        }
      }
    }
    promptText = promptText.trim();

    if (!promptText) {
      return res.status(500).json({ error: `Prompt '${name}' returned empty content.` });
    }

    // 3. Feed the resolved prompt through the same agentic loop as /api/question
    const answer = await agenticAnswer(promptText);
    res.json({ prompt: name, args, answer, timestamp: new Date().toISOString() });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ────────────────────────────────────────────────────────────────────────────
// Boot
// ────────────────────────────────────────────────────────────────────────────

initMCP()
  .then(() => {
    app.listen(PORT, () => {
      console.log(`\n✅  Codebase Intelligence Agent →  http://localhost:${PORT}\n`);
    });
  })
  .catch(err => {
    console.error('[MCP] Initialisation failed:', err.message);
    // Start anyway — health endpoint still works, helpful for debugging
    app.listen(PORT, () => {
      console.log(`⚠️  Server started (MCP init failed) →  http://localhost:${PORT}`);
    });
  });

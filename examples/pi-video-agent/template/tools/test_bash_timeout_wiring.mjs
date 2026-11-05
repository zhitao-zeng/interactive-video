// Regression for the S2E03A failure: a native Pi bash call with no timeout ran 3h19m to the wall
// deadline and the episode stopped with no film. The wiring must bound that call.
//
// Run under either runtime; production uses bun 1.3.14 and the hook must work there too:
//   node tools/test_bash_timeout_wiring.mjs [/path/to/node_modules]
//   bun  tools/test_bash_timeout_wiring.mjs [/path/to/node_modules]
// The fake SSE transport is injected the way each runtime supports: Node needs
// NODE_OPTIONS=--import, Bun accepts an explicit --preload. Passing Node's flag to Bun does
// nothing, which is why an unbounded call would silently reach the real network there.
// Requires Node >= 22 (the SDK imports node:fs globSync) or bun; older Node skips.
// No network, no paid calls.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '../../../..');
const DEPS = process.argv[2] || process.env.PI_STARTER_NODE_MODULES;
const DEFAULT_SECONDS = Number(process.env.PI_TEST_BASH_TIMEOUT || 30);

let failures = 0;
const ok = (label, detail = '') => console.log(`ok   ${label}${detail ? ' — ' + detail : ''}`);
const bad = (label, detail = '') => { failures++; console.log(`FAIL ${label}${detail ? ' — ' + detail : ''}`); };

function makeWorkspace(root, { wallSeconds, bashTimeout = DEFAULT_SECONDS }) {
  const work = path.join(root, 'work');
  fs.cpSync(path.join(HERE, '..'), work, { recursive: true, filter: src => !src.includes('node_modules') });
  fs.symlinkSync(DEPS, path.join(work, 'tools/node_modules'), 'dir');
  for (const dir of ('input', 'output')) fs.mkdirSync(path.join(work, dir), { recursive: true });
  fs.writeFileSync(path.join(work, 'SYSTEM.md'), 'You are a test agent. Use bash when asked.');
  fs.writeFileSync(path.join(work, 'TASK.md'), 'Run the command you are given, then answer done.');
  fs.writeFileSync(path.join(work, 'PRODUCTION.md'), 'test');
  fs.writeFileSync(path.join(work, 'local.json'), JSON.stringify({
    sdk_root: REPO, python: process.execPath, dependencies: DEPS,
    context_window: 1048576, max_output_tokens: 128000,
    wall_seconds: wallSeconds, max_model_requests: 8,
    asr_python: '/bin/true', asr_model_dir: '/tmp',
    audio_service_url: '', novel_asset_publish_dir: '',
    media_lane: '', fasth3_endpoint: '', first_frame_policy: 'none',
    interaction_page: 'none', h3_mode: 'local', viewpoint: 'auto',
    max_request_bytes: 10485760, keep_recent_images: 4,
    bash_timeout_seconds: bashTimeout,
  }, null, 2));
  const deadline = Date.now() + wallSeconds * 1000;
  fs.writeFileSync(path.join(work, 'run-budget.json'), JSON.stringify({
    started_at: Date.now(), deadline_at: deadline, request_limit: 8,
    provider_requests: 0, assistant_messages: 0,
    automatic_continuations: 0, body_compactions: 0,
  }, null, 2));
  return { work, deadline };
}

function fakeTransport(work, { command, timeout }) {
  const preload = path.join(work, 'fake.mjs');
  const args = timeout === undefined ? { command } : { command, timeout };
  fs.writeFileSync(preload, `import fs from 'node:fs';
let calls = 0;
globalThis.fetch = async (url, options = {}) => {
  if (!String(url).startsWith('http://127.0.0.1:1/')) throw new Error('offline test forbids network');
  const body = JSON.parse(options.body); calls++;
  fs.writeFileSync(process.env.PI_RUN_DIR + '/request-' + calls + '.json', JSON.stringify(body));
  const common = { id: 'x', object: 'chat.completion.chunk', model: body.model, created: 1 };
  const delta = calls === 1
    ? { tool_calls: [{ index: 0, id: 'c1', type: 'function',
        function: { name: 'bash', arguments: ${JSON.stringify(JSON.stringify(args))} } }] }
    : { content: 'done' };
  const done = calls > 1;
  const chunks = [
    { ...common, choices: [{ index: 0, delta: { role: 'assistant', ...delta }, finish_reason: null }] },
    { ...common, choices: [{ index: 0, delta: {}, finish_reason: done ? 'stop' : 'tool_calls' }],
      usage: { prompt_tokens: 5, completion_tokens: 5, total_tokens: 10 } },
  ];
  return new Response(chunks.map(c => 'data: ' + JSON.stringify(c) + '\\n\\n').join('') + 'data: [DONE]\\n\\n',
    { headers: { 'content-type': 'text/event-stream' } });
};`);
  return preload;
}

const ON_BUN = Boolean(process.versions.bun);
// The SDK imports `globSync` from node:fs (Node >= 22). Older Node cannot even load the agent,
// so report that as a skip rather than a wiring failure -- the hook is not what broke.
const NODE_MAJOR = Number((process.versions.node || '0').split('.')[0]);
if (!ON_BUN && NODE_MAJOR < 22) {
  console.log(`skip: node ${process.versions.node} cannot load the SDK (needs Node >= 22 or bun)`);
  process.exit(0);
}

/** Spawn the native agent with the fake transport loaded the way this runtime supports. */
function launch(work, deadline, preload) {
  const agent = path.join(work, 'tools/agent.mjs');
  const env = { ...process.env, PI_RUN_DIR: work, PI_PROJECT_ROOT: REPO,
    PI_PROBE_MODEL: 'offline', PI_PROBE_BASE: 'http://127.0.0.1:1/v1', PI_PROBE_API_KEY: 'offline',
    PI_DEADLINE_EPOCH: String(deadline / 1000) };
  if (ON_BUN) {
    // Bun ignores NODE_OPTIONS=--import; it needs an explicit --preload before the entrypoint.
    delete env.NODE_OPTIONS;
    return { command: process.execPath, args: ['--preload', preload, agent], env };
  }
  env.NODE_OPTIONS = '--import ' + preload;
  return { command: process.execPath, args: [agent], env };
}

async function runSession(work, deadline, preload) {
  const started = Date.now();
  const { command, args, env } = launch(work, deadline, preload);
  const child = spawn(command, args, {
    cwd: work,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stderr = '';
  child.stderr.on('data', d => { stderr += d.toString(); });
  const guard = setTimeout(() => child.kill("SIGKILL"), Math.max(20000, DEFAULT_SECONDS * 1000 + 30000));
  const code = await new Promise(r => child.on('exit', r));
  clearTimeout(guard);
  if (code !== 0) console.error(`child exit ${code}:\n${stderr.slice(-1500)}`);
  return { code, stderr, elapsed: (Date.now() - started) / 1000 };
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bash-timeout-wiring-'));
console.log(`runtime: ${ON_BUN ? 'bun ' + process.versions.bun : 'node ' + process.versions.node}`);
try {
  if (!DEPS || !fs.existsSync(DEPS)) {
    console.log(`skip: no node_modules at ${DEPS ?? '(unset)'}`);
    process.exit(0);
  }

  // Case 1 — the S2E03A shape: no timeout at all, command would hang for 600s.
  {
    const { work, deadline } = makeWorkspace(path.join(tmp, 'no-timeout'), { wallSeconds: 300 });
    const preload = fakeTransport(work, { command: 'sleep 600' });
    const { stderr, elapsed } = await runSession(work, deadline, preload);
    const line = (stderr.match(/\[bash-timeout\][^\n]*/) || [''])[0];
    const clamped = line.includes('->') ? Number(line.match(/-> (\d+)s/)[1]) : null;
    if (!line) bad('untimed bash was not clamped');
    else if (clamped !== DEFAULT_SECONDS) bad('untimed bash clamped to wrong value', `${clamped}s`);
    else ok('untimed bash clamped to the configured default', `${clamped}s`);
    if (elapsed > DEFAULT_SECONDS + 45) bad('session outlived the bound', `${elapsed.toFixed(1)}s`);
    else ok('session ended near the bound instead of hanging', `${elapsed.toFixed(1)}s`);
  }

  // Case 2 — explicit timeout longer than the wall clock left must be cut down.
  {
    const { work, deadline } = makeWorkspace(path.join(tmp, 'clamp'), { wallSeconds: 120, bashTimeout: 900 });
    const preload = fakeTransport(work, { command: 'sleep 1', timeout: 3600 });
    const { stderr } = await runSession(work, deadline, preload);
    const line = (stderr.match(/\[bash-timeout\][^\n]*/) || [''])[0];
    const clamped = line.includes('->') ? Number(line.match(/-> (\d+)s/)[1]) : null;
    if (clamped === null) bad('explicit timeout above the deadline was not clamped');
    else if (clamped > 120) bad('clamped above the remaining wall clock', `${clamped}s > 120s`);
    else ok('explicit timeout cut to the remaining wall clock', `${clamped}s of 3600s`);
  }

  // Case 3 — an explicit timeout that already fits is left alone.
  {
    const { work, deadline } = makeWorkspace(path.join(tmp, 'fits'), { wallSeconds: 900, bashTimeout: 900 });
    const preload = fakeTransport(work, { command: 'sleep 1', timeout: 120 });
    const { stderr } = await runSession(work, deadline, preload);
    if (/\[bash-timeout\]/.test(stderr)) bad('fitting explicit timeout was rewritten');
    else ok('fitting explicit timeout left unchanged', '120s of 900s left');
  }
} finally {
  fs.rmSync(tmp, { recursive: true, force: true });
}

console.log(failures === 0 ? 'ALL PASS' : `${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);

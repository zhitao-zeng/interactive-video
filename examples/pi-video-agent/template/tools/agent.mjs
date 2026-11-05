// Native Pi session only: no movie stages, clip schema, finish tool, or acceptance router.
import fs from 'node:fs';
import path from 'node:path';
import { makeSession } from './runtime.mjs';

const directory = path.resolve(process.env.PI_RUN_DIR ?? process.cwd());
const config = JSON.parse(fs.readFileSync(path.join(directory, 'local.json'), 'utf8'));
const pointer = path.join(directory, 'session-path.txt');
const resume = process.argv.includes('--resume');
if (fs.existsSync(pointer) && !resume) throw new Error('Session exists: use --resume; init never overwrites workspace edits.');
if (resume && !fs.existsSync(pointer)) throw new Error('No saved session to resume.');
const { session, agent } = await makeSession({
  directory, config,
  systemPrompt: fs.readFileSync(path.join(directory, 'SYSTEM.md'), 'utf8'),
  sessionFile: resume ? fs.readFileSync(pointer, 'utf8').trim() : undefined,
});
fs.writeFileSync(pointer, session.sessionFile + '\n');
const events = fs.createWriteStream(path.join(directory, 'events.jsonl'), { flags: 'a' });
session.subscribe(event => {
  // Native session JSONL holds the complete history, including tools and compactions.
  if (event.type === 'tool_execution_start') console.log(`tool: ${event.toolName}`);
  if (event.type === 'message_end' && event.message?.role === 'assistant') {
    const text = event.message.content.filter(b => b.type === 'text').map(b => b.text).join('\n');
    if (text) console.log(text);
  }
  if (['auto_compaction_start', 'auto_compact_start', 'auto_retry_start', 'tool_execution_end'].includes(event.type)) {
    events.write(JSON.stringify({ time: new Date().toISOString(), type: event.type,
      tool: event.toolName, isError: event.isError }) + '\n');
  }
});
const promptIndex = process.argv.indexOf('--prompt');
const prompt = promptIndex >= 0 ? process.argv[promptIndex + 1]
  : resume ? '继续本会话尚未完成的任务。先查已有请求、结果与工作记录，沿用原约束，不重复提交。'
  : fs.readFileSync(path.join(directory, 'TASK.md'), 'utf8');
try {
  await session.prompt(prompt);
  await session.waitForIdle();
  if (agent.state.errorMessage) throw new Error(agent.state.errorMessage);
} catch (error) {
  console.error(String(error.message).replace(/https?:\/\/\S+/g, '<remote-url>'));
  process.exitCode = 1;
} finally {
  // A clean session exit is not a claim that a movie exists or passed review.
  events.end();
  session.dispose();
}

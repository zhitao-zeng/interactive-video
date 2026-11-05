// Native Pi session only: no movie stages, clip schema, finish tool, or acceptance router.
import fs from 'node:fs';
import path from 'node:path';
import { makeSession } from './runtime.mjs';
import { runBudget, redactSecrets } from './run_budget.mjs';

const directory = path.resolve(process.env.PI_RUN_DIR ?? process.cwd());
const config = JSON.parse(fs.readFileSync(path.join(directory, 'local.json'), 'utf8'));
const budget = runBudget(directory,config);
const pointer = path.join(directory, 'session-path.txt');
const resume = process.argv.includes('--resume');
if (fs.existsSync(pointer) && !resume) throw new Error('Session exists: use --resume; init never overwrites workspace edits.');
if (resume && !fs.existsSync(pointer)) throw new Error('No saved session to resume.');
const { session, agent } = await makeSession({
  directory, config, budget,
  systemPrompt: fs.readFileSync(path.join(directory, 'SYSTEM.md'), 'utf8'),
  sessionFile: resume ? fs.readFileSync(pointer, 'utf8').trim() : undefined,
});
fs.writeFileSync(pointer, session.sessionFile + '\n');
const events = fs.createWriteStream(path.join(directory, 'events.jsonl'), { flags: 'a' });
session.subscribe(event => {
  // Native session JSONL holds the complete history, including tools and compactions.
  if (event.type === 'tool_execution_start') console.log(`tool: ${event.toolName}`);
  if (event.type === 'message_end' && event.message?.role === 'assistant') {
    budget.state.assistant_messages++;budget.save();
    const text = event.message.content.filter(b => b.type === 'text').map(b => b.text).join('\n');
    if (text) console.log(text);
  }
  if (['auto_compaction_start', 'auto_compact_start', 'auto_retry_start', 'tool_execution_end'].includes(event.type)) {
    events.write(JSON.stringify({ time: new Date().toISOString(), type: event.type,
      tool: event.toolName, isError: event.isError }) + '\n');
  }
});
const promptIndex = process.argv.indexOf('--prompt');
const taskPrompt = promptIndex >= 0 ? process.argv[promptIndex + 1]
  : resume ? '继续本会话尚未完成的任务。先查已有请求、结果与工作记录，沿用原约束，不重复提交。'
  : fs.readFileSync(path.join(directory, 'TASK.md'), 'utf8');
// Put the launch choices in the first request; resumed sessions retain their original context.
const prompt = resume ? taskPrompt
  : fs.readFileSync(path.join(directory, 'PRODUCTION.md'), 'utf8') + '\n---\n\n' + taskPrompt;
let failure=null;
const deadlineTimer=setInterval(()=>{
  if(Date.now()>=budget.state.deadline_at && !agent.state.pendingToolCalls?.size)void session.abort();
},1000);
try {
  budget.check();
  await session.prompt(prompt);
  await session.waitForIdle();
  if (agent.state.errorMessage?.includes('REQUEST_BODY_TOO_LARGE') && budget.state.body_compactions<1) {
    budget.check();budget.state.body_compactions++;budget.save();
    await session.compact('保留原任务约束、已采用素材、当前问题和文件路径。压缩重复工具日志，不改原文。');
    await session.prompt('请求体过大已触发原生压缩。沿用本任务原额度继续，不重复提交已有媒体。');
    await session.waitForIdle();
  }
  if (agent.state.errorMessage) throw new Error(agent.state.errorMessage);
} catch (error) {
  failure=String(error.message).replace(/https?:\/\/\S+/g, '<remote-url>');
  console.error(failure);
  process.exitCode = 1;
} finally {
  clearInterval(deadlineTimer);
  budget.state.ended_at=Date.now();budget.state.error=failure;
  budget.state.elapsed_seconds=(budget.state.ended_at-budget.state.started_at)/1000;
  budget.state.wall_deadline_exceeded=Date.now()>=budget.state.deadline_at;
  if(budget.state.wall_deadline_exceeded)process.exitCode=1;
  budget.save();
  fs.writeFileSync(path.join(directory,'agent-run.json'),JSON.stringify({
    ...budget.state,session_file:session.sessionFile,model:agent.state.model.id,
    status:failure||budget.state.wall_deadline_exceeded?'stopped':'session_ended',
    note:'Process status is not movie acceptance; inspect output/delivery.json and its evidence.',
  },null,2)+'\n');
  // A clean session exit is not a claim that a movie exists or passed review.
  events.end();
  session.dispose();
}

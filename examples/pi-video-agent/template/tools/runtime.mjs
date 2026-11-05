import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { redactSecrets } from './run_budget.mjs';
import { createProvider, envApiKeyAuth, InMemoryCredentialStore } from '@earendil-works/pi-ai';
import { openAICompletionsApi } from '@earendil-works/pi-ai/api/openai-completions.lazy';
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import { applyRequestBudget } from './request_budget.mjs';
import { resolveBashTimeout, remainingSeconds, timeoutSource } from './bash_limits.mjs';

export async function makeSession({ directory, config, budget, systemPrompt, mechanical = false, maxTokens = config.max_output_tokens,
  onResponseModel = () => {}, onRequest = () => {}, sessionFile } = {}) {
  const model = { id: process.env.PI_PROBE_MODEL, name: process.env.PI_PROBE_MODEL, provider: 'probe',
    api: 'openai-completions', baseUrl: process.env.PI_PROBE_BASE, reasoning: !mechanical,
    input: ['text', 'image'], contextWindow: config.context_window, maxTokens,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, // Unknown prices, not a bill.
    compat: { supportsDeveloperRole: false, supportsReasoningEffort: false, maxTokensField: 'max_tokens',
      thinkingFormat: 'deepseek', requiresReasoningContentOnAssistantMessages: true } };
  const modelRuntime = await ModelRuntime.create({ credentials: new InMemoryCredentialStore(),
    modelsPath: null, refreshOnCreate: false, allowModelNetwork: false });
  modelRuntime.registerNativeProvider(createProvider({ id: 'probe', models: [model], api: openAICompletionsApi(),
    auth: { apiKey: envApiKeyAuth('Task-scoped key', ['PI_PROBE_API_KEY']) } }));
  const settingsManager = SettingsManager.inMemory({ compaction: { enabled: true },
    retry: { enabled: true, maxRetries: 2, provider: { maxRetries: 0 } }, cacheWarming: 'off' });
  const resourceLoader = new DefaultResourceLoader({ cwd: directory, agentDir: directory, settingsManager,
    systemPrompt, noExtensions: true, noSkills: true, additionalSkillPaths: [path.join(directory, config.skill_path ?? 'skills/episode-production')], noPromptTemplates: true, noThemes: true, noContextFiles: true });
  await resourceLoader.reload();
  const sessionManager = sessionFile
    ? SessionManager.open(path.resolve(sessionFile), directory, directory)
    : SessionManager.create(directory, directory);
  const { session } = await createAgentSession({ cwd: directory, agentDir: directory, modelRuntime, model,
    thinkingLevel: mechanical ? 'off' : 'medium', settingsManager, sessionManager, resourceLoader });
  const agent = session.agent;
  const secrets=Object.entries(process.env).filter(([name,value])=>/KEY|TOKEN|PASSWORD|SECRET/.test(name)&&value.length>=12).map(([,value])=>value);
  const originalAfter=agent.afterToolCall;
  const originalBefore=agent.beforeToolCall;

  // The native bash tool installs no timer when `timeout` is absent, so one careless command can
  // outlive the whole run: S2E03A lost 3h19m to a single `find /` with no timeout and stopped at
  // the wall deadline with no film. Bound every bash call here -- untimed calls get a default, and
  // no call may exceed the wall clock this run has left. Long media steps keep working because
  // they pass their own explicit timeout, which is only ever shortened when the run is ending.
  agent.beforeToolCall=async(context,signal)=>{
    if(budget&&Date.now()>=budget.state.deadline_at)return {block:true,terminate:true,reason:'Original run deadline reached; no new tool dispatch.'};
    const blocked=await originalBefore?.(context,signal);
    if(blocked?.block)return blocked;
    if(context?.toolCall?.name==='bash'&&context.args&&typeof context.args==='object'){
      const requested=context.args.timeout;
      const bounded=resolveBashTimeout({requested,remainingSeconds:remainingSeconds(budget?.state?.deadline_at??Infinity),
        defaultSeconds:Number(config.bash_timeout_seconds)||undefined});
      if(bounded!==requested){
        const source=timeoutSource(requested);
        const why=source==='default'?'no timeout given; bounded default':'requested timeout exceeds the remaining wall clock';
        context.args.timeout=bounded;
        console.error(`[bash-timeout] ${source} ${requested??'(none)'}s -> ${bounded}s (${why})`);
      }
    }
    return blocked;
  };
  agent.afterToolCall=async(context,signal)=>{
    const override=await originalAfter?.(context,signal);
    const result={...context.result,...override};
    const content=redactSecrets(result.content,secrets);
    if(budget)content.push({type:'text',text:`Run facts: ${budget.state.provider_requests}/${budget.state.request_limit} provider requests; ${Math.max(0,Math.floor((budget.state.deadline_at-Date.now())/60000))} minutes remain. Original limits persist across resume.`});
    return {...override,content,details:redactSecrets(result.details,secrets),
      ...(result.structuredContent?{structuredContent:redactSecrets(result.structuredContent,secrets)}:{})};
  };
  // Native execution policy orders edits and dependent bash calls. No custom film tools.
  // Instrument the native transport, including compact summaries; session owns all continuations.
  const stream = agent.streamFunction;
  agent.streamFunction = (requestModel, context, options = {}) => stream(requestModel, context, {
    ...options, maxTokens,
    onPayload: async payload => {
      payload.thinking = { type: mechanical ? 'disabled' : 'enabled' }; payload.temperature = 1;
      const requestBudget = await applyRequestBudget(payload, {
        maxBytes: Number(config.max_request_bytes || 10485760),
        keepRecentImages: Number(config.keep_recent_images || 4),
        resizeImage: block=>{
          const result=spawnSync(config.python,[path.join(directory,'tools/image_payload.py')],
            {input:JSON.stringify(block),encoding:'utf8',maxBuffer:24*1024*1024});
          if(result.status!==0)throw new Error('Image transport resize failed: '+result.stderr.slice(-500));
          return JSON.parse(result.stdout);
        },
      });
      if (requestBudget.removedImages || requestBudget.resizedImages) {
        console.error('[request-budget] ' + JSON.stringify(requestBudget));
      }
      budget?.reserve();
      await onRequest({ model: payload.model, max_tokens: payload.max_tokens, thinking: payload.thinking,
        reasoning_effort: payload.reasoning_effort ?? null, temperature: payload.temperature,
        tool_count: payload.tools?.length ?? 0, message_count: payload.messages?.length ?? 0 });
      return await options.onPayload?.(payload);
    },
    onProviderStreamEvent: async (event, model) => {
      if (event?.model) onResponseModel(event.model);
      await options.onProviderStreamEvent?.(event, model);
    },
  });
  return { session, agent, loadedSkills: resourceLoader.getSkills().skills.map(skill => ({ name: skill.name, filePath: skill.filePath })) };
}

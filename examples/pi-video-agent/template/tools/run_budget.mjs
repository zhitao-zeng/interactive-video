import fs from 'node:fs';
import path from 'node:path';

export function runBudget(directory,config,now=()=>Date.now()) {
  const filename=path.join(directory,'run-budget.json');
  const state=fs.existsSync(filename)?JSON.parse(fs.readFileSync(filename,'utf8')):{
    started_at:now(),deadline_at:Number(process.env.PI_DEADLINE_EPOCH)*1000||now()+Number(config.wall_seconds??14400)*1000,
    request_limit:Number(config.max_model_requests??256),provider_requests:0,assistant_messages:0,
    automatic_continuations:0,body_compactions:0,
  };
  function save(){const temp=filename+'.partial';fs.writeFileSync(temp,JSON.stringify(state,null,2)+'\n');fs.renameSync(temp,filename);}
  function check(){
    if(now()>=state.deadline_at)throw new Error('RUN_BUDGET_EXHAUSTED: original wall deadline reached.');
    if(state.provider_requests>=state.request_limit)throw new Error('RUN_BUDGET_EXHAUSTED: original provider request limit reached.');
  }
  save();
  return {state,save,check,reserve(){check();state.provider_requests++;state.last_request_at=now();save();}};
}

export function redactSecrets(value,secrets) {
  if(typeof value==='string') {
    for(const secret of secrets)if(secret.length>=12)value=value.split(secret).join('[REDACTED_CREDENTIAL]');
    return value;
  }
  if(Array.isArray(value))return value.map(x=>redactSecrets(x,secrets));
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,k==='data'?v:redactSecrets(v,secrets)]));
  return value;
}

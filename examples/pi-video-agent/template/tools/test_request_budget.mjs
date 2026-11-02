import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { applyRequestBudget } from './request_budget.mjs';
import { runBudget, redactSecrets } from './run_budget.mjs';
const image=n=>({type:'image',data:'a'.repeat(n),mimeType:'image/jpeg'});
const small={messages:[{role:'user',content:[image(10),{type:'text',text:'source'}]}]};
assert.equal((await applyRequestBudget(small,{maxBytes:1000})).removedImages,0);
const many={messages:Array.from({length:10},()=>({role:'user',content:[image(100000)]}))};
const result=await applyRequestBudget(many,{maxBytes:350000,keepRecentImages:2});
assert.equal(result.removedImages,8);assert.equal(many.messages[9].content[0].type,'image');
const recent={messages:[{role:'user',content:Array.from({length:4},()=>image(150000))}]};
await assert.rejects(()=>applyRequestBudget(structuredClone(recent),{maxBytes:350000}),/REQUEST_BODY_TOO_LARGE/);
const resized=await applyRequestBudget(recent,{maxBytes:350000,resizeImage:()=>image(10000)});
assert.equal(resized.resizedImages,4);assert.equal(recent.messages[0].content.length,4);
for(const content of ['a'.repeat(20000),[{type:'text',text:'a'.repeat(20000)}]]){
 const p={messages:[{role:'user',content}]};const original=JSON.stringify(p);
 await assert.rejects(()=>applyRequestBudget(p,{maxBytes:5000}),/REQUEST_BODY_TOO_LARGE/);
 assert.equal(JSON.stringify(p),original);
}
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pi-budget-test-'));let now=10000;
const inherited=process.env.PI_DEADLINE_EPOCH;delete process.env.PI_DEADLINE_EPOCH;
try{
 const b=runBudget(dir,{max_model_requests:2,wall_seconds:10},()=>now);
 b.reserve();b.reserve();assert.throws(()=>b.reserve(),/request limit/);
 const resumed=runBudget(dir,{max_model_requests:900,wall_seconds:900},()=>now);
 assert.equal(resumed.state.provider_requests,2);assert.equal(resumed.state.request_limit,2);
 now=21000;assert.throws(()=>resumed.check(),/wall deadline/);
 assert.equal(redactSecrets('x=private-secret-12345',['private-secret-12345']),'x=[REDACTED_CREDENTIAL]');
 console.log(JSON.stringify({ok:true,task_text_preserved:true,recent_images_resized:true,resume_budget_preserved:true}));
}finally{fs.rmSync(dir,{recursive:true});if(inherited!==undefined)process.env.PI_DEADLINE_EPOCH=inherited;}

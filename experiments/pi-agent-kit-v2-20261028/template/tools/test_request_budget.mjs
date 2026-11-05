import { applyRequestBudget } from './request_budget.mjs';

const image = (size) => ({type: 'image', data: 'x'.repeat(size), mimeType: 'image/jpeg'});
const sizeOf = (payload) => Buffer.byteLength(JSON.stringify(payload), 'utf8');

// Under budget: untouched.
const small = {messages: [{role: 'user', content: [image(1000)]}]};
let result = applyRequestBudget(small, {maxBytes: 100000, keepRecentImages: 4});
if (result.removedImages !== 0 || small.messages[0].content[0].type !== 'image') {
  throw new Error('under-budget payload changed');
}

// Over budget: keep the last two images, replace older blocks with text.
const payload = {messages: Array.from({length: 10}, (_, i) => ({
  role: i % 2 ? 'assistant' : 'user',
  content: [image(100000), {type: 'text', text: 'shot ' + i}],
}))};
result = applyRequestBudget(payload, {maxBytes: 350000, keepRecentImages: 2});
if (result.removedImages !== 8) throw new Error('removed ' + result.removedImages + ' images, expected 8');
if (payload.messages[0].content[0].type !== 'text') throw new Error('old image not replaced');
if (payload.messages[8].content[0].type !== 'image' || payload.messages[9].content[0].type !== 'image') {
  throw new Error('recent images were not kept');
}
if (sizeOf(payload) > 350000) throw new Error('still over budget: ' + sizeOf(payload));

// Text-only oversize: old long text is truncated, recent messages are kept.
const textPayload = {messages: [
  {role: 'user', content: [{type: 'text', text: 'a'.repeat(20000)}]},
  {role: 'assistant', content: [{type: 'text', text: 'b'.repeat(20000)}]},
  {role: 'assistant', content: [{type: 'text', text: 'recent-1'}]},
  {role: 'user', content: [{type: 'text', text: 'recent-2'}]},
]};
result = applyRequestBudget(textPayload, {maxBytes: 5000, keepRecentImages: 4});
if (result.truncatedText < 1) throw new Error('old text was not truncated');
if (!textPayload.messages[0].content[0].text.includes('[truncated for request budget]')) {
  throw new Error('truncation marker missing');
}
if (textPayload.messages[3].content[0].text !== 'recent-2') throw new Error('recent message changed');

console.log(JSON.stringify({ok: true, removedImages: 8, truncatedText: result.truncatedText}));

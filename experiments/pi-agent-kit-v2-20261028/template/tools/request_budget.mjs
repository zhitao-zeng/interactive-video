// Request-body budget: keep recent images, replace older image blocks with text.
// The Pi session JSONL is never modified; this only shapes one provider request.

const PLACEHOLDER = '[older image omitted from this request; the original remains in the session log]';

function isImage(block) {
  return block && typeof block === 'object'
    && (block.type === 'image' || block.type === 'image_url');
}

function imagePayload(block) {
  if (block.type === 'image_url') return block.image_url?.url || '';
  return block.data || '';
}

function collectImages(payload) {
  const found = [];
  const messages = Array.isArray(payload?.messages) ? payload.messages : [];
  messages.forEach((message, messageIndex) => {
    const content = message?.content;
    if (!Array.isArray(content)) return;
    content.forEach((block, blockIndex) => {
      if (isImage(block)) found.push({messageIndex, blockIndex, block});
    });
  });
  return found;
}

function replaceImage(block) {
  if (block.type === 'image_url') {
    return {type: 'text', text: PLACEHOLDER};
  }
  return {type: 'text', text: PLACEHOLDER};
}

function truncateText(payload, maxMessagesToSkip = 2) {
  const messages = Array.isArray(payload?.messages) ? payload.messages : [];
  let truncated = 0;
  const lastIndex = messages.length - 1;
  for (let i = 0; i <= lastIndex - maxMessagesToSkip; i += 1) {
    const content = messages[i]?.content;
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      if (block?.type === 'text' && typeof block.text === 'string' && block.text.length > 4000) {
        block.text = block.text.slice(0, 1200) + '\n…[truncated for request budget]';
        truncated += 1;
      }
    }
  }
  return truncated;
}

export function applyRequestBudget(payload, {maxBytes = 7500000, keepRecentImages = 4} = {}) {
  if (!payload || !Array.isArray(payload.messages)) {
    return {bytes: 0, removedImages: 0, truncatedText: 0, skipped: true};
  }
  const size = () => Buffer.byteLength(JSON.stringify(payload), 'utf8');
  let bytes = size();
  if (bytes <= maxBytes) return {bytes, removedImages: 0, truncatedText: 0};

  const images = collectImages(payload);
  const keep = Math.max(0, Number(keepRecentImages) || 0);
  const dropCount = Math.max(0, images.length - keep);
  let removedImages = 0;
  for (const item of images.slice(0, dropCount)) {
    const message = payload.messages[item.messageIndex];
    message.content[item.blockIndex] = replaceImage(item.block);
    removedImages += 1;
  }
  bytes = size();
  let truncatedText = 0;
  if (bytes > maxBytes) {
    truncatedText = truncateText(payload);
    bytes = size();
  }
  return {bytes, removedImages, truncatedText};
}

// Shape a transport copy, never truncate task/source text or edit native history.
export async function applyRequestBudget(payload, {maxBytes=10485760, keepRecentImages=4, resizeImage}={}) {
  const size=()=>Buffer.byteLength(JSON.stringify(payload),'utf8');
  let bytes=size(),removedImages=0,resizedImages=0;
  if(bytes<=maxBytes)return {bytes,removedImages,resizedImages};
  const images=[];
  for(const message of payload.messages??[]) {
    if(!Array.isArray(message.content))continue;
    for(let i=0;i<message.content.length;i++) {
      const block=message.content[i];
      if(['image','image_url'].includes(block?.type))images.push({message,index:i,block});
    }
  }
  const keep=Math.max(1,Number(keepRecentImages)||4);
  for(const {message,index} of images.slice(0,Math.max(0,images.length-keep))) {
    message.content[index]={type:'text',text:'[Older image omitted from this request. Original file and prior observation remain; use read if needed.]'};
    removedImages++;
  }
  bytes=size();
  if(bytes>maxBytes&&resizeImage) {
    for(const {message,index,block} of images.slice(-keep)) {
      message.content[index]=await resizeImage(block);
      resizedImages++;
    }
    bytes=size();
  }
  if(bytes>maxBytes)throw new Error(`REQUEST_BODY_TOO_LARGE: ${bytes} > ${maxBytes}; full task/source text retained. Compact the native session before retrying.`);
  return {bytes,removedImages,resizedImages};
}

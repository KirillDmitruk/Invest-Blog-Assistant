const encoder=new TextEncoder();const AAD=encoder.encode('content-hub-walt-v1');
const bytes=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-s.length%4)%4)),c=>c.charCodeAt(0));
const b64=a=>btoa(String.fromCharCode(...a)).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_');
const equal=(a,b)=>{let d=a.length^b.length;for(let i=0;i<Math.max(a.length,b.length);i++)d|=(a.charCodeAt(i)||0)^(b.charCodeAt(i)||0);return d===0;};
export async function verifyButton(value,owner,key,now=Date.now()/1000){
 if(typeof value!=='string'||encoder.encode(value).length>64||!value.startsWith('wb:'))throw Error('button');
 const p=value.lastIndexOf(':'),body=value.slice(3,p),sig=value.slice(p+1);
 const h=await crypto.subtle.importKey('raw',encoder.encode(key),{name:'HMAC',hash:'SHA-256'},false,['sign']);
 const expected=b64(new Uint8Array(await crypto.subtle.sign('HMAC',h,encoder.encode(body+':'+owner))).slice(0,9));
 if(!equal(sig,expected))throw Error('signature');
 const fields=body.split(':'),action=fields[0],expiry=Number(fields.at(-1));
 if(!Number.isSafeInteger(expiry)||expiry<=now||expiry>now+86430)throw Error('expiry');
 if(action==='q'){if(fields.length!==5||!/^\d+$/.test(fields[1])||!/^\d+$/.test(fields[2])||!['l','s'].includes(fields[3]))throw Error('quote');}
 else if(['e','c','r'].includes(action)){if(fields.length!==3||!/^[a-f0-9]{16}$/.test(fields[1]))throw Error('draft');}
 else if(action==='n'){if(fields.length!==3||!/^\d+$/.test(fields[1]))throw Error('next');}
 else throw Error('action');
 return fields;
}
export async function seal(event,key){
 const nonce=crypto.getRandomValues(new Uint8Array(12));
 const aes=await crypto.subtle.importKey('raw',bytes(key),'AES-GCM',false,['encrypt']);
 const value=new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM',iv:nonce,additionalData:AAD},aes,encoder.encode(JSON.stringify(event))));
 const result=new Uint8Array(nonce.length+value.length);result.set(nonce);result.set(value,12);return 'v1.'+b64(result);
}
export async function handle(request,env,fetcher=fetch){
 if(request.method==='GET')return new Response('Walt callback gateway',{status:200});
 if(request.method!=='POST'||!env.TG_WEBHOOK_SECRET||!equal(request.headers.get('X-Telegram-Bot-Api-Secret-Token')||'',env.TG_WEBHOOK_SECRET))return new Response('Forbidden',{status:403});
 if(Number(request.headers.get('content-length')||0)>20000)return new Response('Too large',{status:413});
 const raw=await request.text();if(raw.length>20000)return new Response('Too large',{status:413});
 let update;try{update=JSON.parse(raw);}catch{return new Response('Invalid JSON',{status:400});}
 const q=update.callback_query,owner=String(env.OWNER_ID);
 const tg=async(method,body)=>fetcher('https://api.telegram.org/bot'+env.TG_BOT_TOKEN+'/'+method,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 if(!q)return new Response('Ignored',{status:200});
 if(String(q.from?.id)!==owner||String(q.message?.chat?.id)!==owner||q.message.chat.type!=='private'||String(q.message?.from?.id)!==String(env.BOT_ID)){
  await tg('answerCallbackQuery',{callback_query_id:q.id,text:'Доступ ограничен.',show_alert:true});return new Response('Ignored',{status:200});
 }
 try{
  if(env.ENABLED!=='true')throw Error('disabled');
  const parts=await verifyButton(q.data,owner,env.WALT_BUTTON_KEY);
  const event={data:q.data,query:q.id,owner,chat:owner,message:String(q.message.message_id),created:Math.floor(Date.now()/1000)};
  let workflow='cloud-walt.yml',inputs={event:await seal(event,env.WALT_EVENT_KEY)};
  if(parts[0]==='n'){
   const digest=new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(q.data)));
   const tag=Array.from(digest.slice(0,8),x=>x.toString(16).padStart(2,'0')).join('');
   workflow='cloud-signals.yml';inputs={send:true,request_tag:tag};
  }
  const result=await fetcher('https://api.github.com/repos/'+env.GITHUB_REPOSITORY+'/actions/workflows/'+workflow+'/dispatches',{
   method:'POST',headers:{Authorization:'Bearer '+env.GH_DISPATCH_TOKEN,Accept:'application/vnd.github+json','Content-Type':'application/json','User-Agent':'ContentHub-Walt'},body:JSON.stringify({ref:'main',inputs})});
  await tg('answerCallbackQuery',{callback_query_id:q.id,text:result.status===204?'Запрос принят. Дождись новой карточки в чате.':'Запуск не подтверждён. Проверь чат и состояние Walt; не повторяй открытие вслепую.',show_alert:result.status!==204});
  return new Response('OK',{status:200});
 }catch{
  await tg('answerCallbackQuery',{callback_query_id:q.id,text:'Кнопка недоступна или устарела. Нужна новая проверка.',show_alert:true});
  return new Response('Ignored',{status:200});
 }
}
export default {fetch:(request,env)=>handle(request,env)};

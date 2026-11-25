// Fixed PAPER workflows only. This Worker has no broker or Telegram credentials.
export async function runScheduled(env,fetcher=fetch,now=Date.now()){
 if(env.TRADING_MODE!=='paper'||env.DEMO_MONITOR_ENABLED!=='true')return {disabled:true};
 if(!/^[\w.-]+\/[\w.-]+$/.test(env.GITHUB_REPOSITORY||'')||!env.GH_DISPATCH_TOKEN)throw Error('Missing scheduler configuration');
 const result={started:0,skipped:0};
 for(const workflow of ['tinvest-paper.yml','paper-perps.yml','cloud-signals.yml','pulse-drafts.yml','telegram-news.yml']){
  if(workflow==='telegram-news.yml'){
   const hour=new Date(now+3*3600000).getUTCHours();
   if(env.CLOUD_NEWS_ENABLED!=='true'||hour<9||hour>=23){result.skipped++;continue;}
  }
  if(workflow==='pulse-drafts.yml'){
   const local=new Date(now+3*3600000),hour=local.getUTCHours(),day=local.getUTCDay();
   const weekly=[1,3,5,6].includes(day)&&hour>=10&&hour<13;
   const monthly=local.getUTCDate()===1&&hour>=11&&hour<14;
   if(!weekly&&!monthly){result.skipped++;continue;}
  }
  const url='https://api.github.com/repos/'+env.GITHUB_REPOSITORY+'/actions/workflows/'+workflow;
  const headers={Authorization:'Bearer '+env.GH_DISPATCH_TOKEN,Accept:'application/vnd.github+json','User-Agent':'ContentHub-DemoScheduler'};
  const check=await fetcher(url+'/runs?per_page=1',{headers});
  if(!check.ok)throw Error('Cannot verify virtual workflow status');
  const data=await check.json(),last=data.workflow_runs?.[0];
  if(workflow==='telegram-news.yml'&&last&&Math.floor(Date.parse(last.created_at)/1800000)===Math.floor(now/1800000)){result.skipped++;continue;}
  const interval=workflow==='telegram-news.yml'?0:workflow==='pulse-drafts.yml'?1800000:workflow==='cloud-signals.yml'?(last?.conclusion==='failure'?1800000:7200000):240000;
  if(last&&(['queued','in_progress','waiting','requested','pending'].includes(last.status)||now-Date.parse(last.created_at)<interval)){result.skipped++;continue;}
  const body=['cloud-signals.yml','telegram-news.yml'].includes(workflow)?{ref:'main',inputs:{send:true}}:workflow==='pulse-drafts.yml'?{ref:'main',inputs:{kind:'auto',send:true}}:{ref:'main'};
  const response=await fetcher(url+'/dispatches',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(response.status!==204)throw Error('Virtual workflow dispatch not confirmed');
  result.started++;
 }
 return result;
}
export default {
 fetch:()=>new Response('Content Hub demo scheduler: PAPER, every 5 minutes',{headers:{'Content-Type':'text/plain'}}),
 scheduled:(_controller,env,ctx)=>ctx.waitUntil(runScheduled(env))
};

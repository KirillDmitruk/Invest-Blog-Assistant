import assert from 'node:assert/strict';
import {runScheduled} from '../cloudflare/demo-scheduler/worker.mjs';
const env={TRADING_MODE:'paper',DEMO_MONITOR_ENABLED:'true',GITHUB_REPOSITORY:'owner/repo',GH_DISPATCH_TOKEN:'test-only'};
const now=Date.now();let calls=[];
const fetcher=async(url,options={})=>{calls.push({url,options});return url.includes('/runs?')?Response.json({workflow_runs:[]}):new Response(null,{status:204})};
assert.deepEqual(await runScheduled(env,fetcher,now),{started:2,skipped:0});
assert.equal(calls.length,4);for(const c of calls.filter(c=>c.options.method==='POST')){assert.match(c.url,/\/(tinvest-paper|paper-perps)\.yml\/dispatches$/);assert.deepEqual(JSON.parse(c.options.body),{ref:'main'})}
calls=[];await runScheduled({...env,TRADING_MODE:'live'},fetcher);assert.equal(calls.length,0);
const busy=async()=>Response.json({workflow_runs:[{status:'in_progress',created_at:new Date(now-600000).toISOString()}]});
assert.deepEqual(await runScheduled(env,busy,now),{started:0,skipped:2});
const recent=async()=>Response.json({workflow_runs:[{status:'completed',created_at:new Date(now-60000).toISOString()}]});
assert.deepEqual(await runScheduled(env,recent,now),{started:0,skipped:2});
await assert.rejects(runScheduled(env,async()=>new Response(null,{status:403}),now));
console.log('Scheduler: paper-only allowlist, two dispatches, recent/busy deduplication and error handling verified.');

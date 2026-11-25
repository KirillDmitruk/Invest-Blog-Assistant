import assert from 'node:assert/strict';
import {runScheduled} from '../cloudflare/demo-scheduler/worker.mjs';
const env={TRADING_MODE:'paper',DEMO_MONITOR_ENABLED:'true',GITHUB_REPOSITORY:'owner/repo',GH_DISPATCH_TOKEN:'test-only'};
const now=Date.parse('2026-11-29T17:00:00Z');let calls=[];
const fetcher=async(url,options={})=>{calls.push({url,options});return url.includes('/runs?')?Response.json({workflow_runs:[]}):new Response(null,{status:204})};
assert.deepEqual(await runScheduled(env,fetcher,now),{started:3,skipped:1});
assert.equal(calls.length,6);for(const c of calls.filter(c=>c.options.method==='POST')){assert.match(c.url,/\/(tinvest-paper|paper-perps|cloud-signals)\.yml\/dispatches$/);assert.deepEqual(JSON.parse(c.options.body),c.url.includes('cloud-signals')?{ref:'main',inputs:{send:true}}:{ref:'main'})}
calls=[];await runScheduled({...env,TRADING_MODE:'live'},fetcher);assert.equal(calls.length,0);
const busy=async()=>Response.json({workflow_runs:[{status:'in_progress',created_at:new Date(now-600000).toISOString()}]});
assert.deepEqual(await runScheduled(env,busy,now),{started:0,skipped:4});
const recent=async()=>Response.json({workflow_runs:[{status:'completed',created_at:new Date(now-60000).toISOString()}]});
assert.deepEqual(await runScheduled(env,recent,now),{started:0,skipped:4});
calls=[];const aging=async(url,options={})=>{if(url.includes('/runs?'))return Response.json({workflow_runs:[{status:'completed',conclusion:'success',created_at:new Date(now-3600000).toISOString()}]});return fetcher(url,options)};
assert.deepEqual(await runScheduled(env,aging,now),{started:2,skipped:2});
assert.ok(calls.every(c=>!c.url.includes('cloud-signals')));
calls=[];assert.deepEqual(await runScheduled(env,fetcher,Date.parse('2026-11-23T07:00:00Z')),{started:4,skipped:0});
assert.deepEqual(JSON.parse(calls.find(c=>c.url.endsWith('pulse-drafts.yml/dispatches')).options.body),{ref:'main',inputs:{kind:'auto',send:true}});
await assert.rejects(runScheduled(env,async()=>new Response(null,{status:403}),now));
console.log('Scheduler: paper-only workflows, reports every two hours, monitor cadence, deduplication and error handling verified.');

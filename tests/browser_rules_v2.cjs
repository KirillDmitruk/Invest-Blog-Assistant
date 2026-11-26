const assert=require('node:assert/strict'),path=require('node:path');
let chromium;try{({chromium}=require('playwright'))}catch{({chromium}=require(path.resolve(path.dirname(process.execPath),'..','node_modules','playwright')))}
(async()=>{const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--proxy-server=direct://','--proxy-bypass-list=*']});
try{const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];let writes=0;
page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')writes++});await page.route('https://cdn.jsdelivr.net/**',r=>r.abort());
const t=await(await page.request.get('http://127.0.0.1:8000/api/trading/overview?refresh=true')).json();assert.equal(t.state.strategy_version,'t-futures-v3-20261126');assert.ok(t.state.strategy_baseline.trade_count>=14);
await page.goto('http://127.0.0.1:8000/#trading');await page.locator('#labAsOf').filter({hasText:/Правила v3/}).waitFor();
await page.locator('[data-lab-tab=settings]').click();await page.locator('#labAssumptions').locator('..').locator('summary').click();assert.match(await page.locator('#labAssumptions').innerText(),/часовая пауза/);
const w=await(await page.request.get('http://127.0.0.1:8000/api/paper/overview?refresh=true')).json();assert.equal(w.state.strategy_version,'walt-v3-20261126');assert.equal(w.state.limits.max_same_side,2);assert.equal(w.state.limits.direction_risk_pct,'2.5');
await page.goto('http://127.0.0.1:8000/#paper');await page.locator('#paperStatus').filter({hasText:/Правила v3/}).waitFor();
await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
assert.equal(writes,0);assert.deepEqual(errors,[]);console.log('Rules v3 visible in both dashboards; preserved baselines, new limits, mobile width and no mutations verified.');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});

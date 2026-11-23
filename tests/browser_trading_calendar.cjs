const assert=require('node:assert/strict'),path=require('node:path');
let chromium;try{({chromium}=require('playwright'))}catch{({chromium}=require(path.resolve(path.dirname(process.execPath),'..','node_modules','playwright')))}
(async()=>{const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--proxy-server=direct://','--proxy-bypass-list=*']});
try{const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];let writes=0;
page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')writes++});await page.route('https://cdn.jsdelivr.net/**',r=>r.abort());
const response=await page.goto('http://127.0.0.1:8000/#trading');assert.equal(response.headers()['cache-control'],'no-store');
await page.locator('#labFreshness strong').first().waitFor();assert.equal(await page.locator('#labPrepare').count(),0);assert.doesNotMatch(await page.locator('#page-trading').innerText(),/Grok/);
assert.match(await page.locator('script[src*="/static/trading.js"]').getAttribute('src'),/\?v=/);
const d=await(await page.request.get('http://127.0.0.1:8000/api/trading/overview')).json();assert.ok(Array.isArray(d.daily_results));
await page.locator('[data-lab-tab=statistics]').click();await page.locator('#labCalendar h2').filter({hasText:'Календарь сделок'}).waitFor();
await page.screenshot({path:'.paper-ui-check/trading-calendar-live.png',fullPage:true});
// Display-only fixture: no fake trades are saved to the account.
d.daily_results=[{date:'2026-11-20',net_pnl:'1250.50',closed:2,wins:2,losses:0,flat:0,unmatched:0},{date:'2026-11-21',net_pnl:'-520.25',closed:1,wins:0,losses:1,flat:0,unmatched:0}];
await page.route('**/api/trading/overview?refresh=true*',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify(d)}));
await page.locator('#labRefresh').click();await page.locator('.calendar-win').waitFor();assert.equal(await page.locator('.calendar-loss').count(),1);
await page.locator('.calendar-win').click();assert.match(await page.locator('.calendar-detail').innerText(),/закрыто 2/);
await page.locator('#page-trading .lab-badge').evaluate(e=>e.textContent='ДЕМОНСТРАЦИЯ КАЛЕНДАРЯ · тестовые дни');
await page.screenshot({path:'.paper-ui-check/trading-calendar-demo-desktop.png',fullPage:true});
await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
await page.screenshot({path:'.paper-ui-check/trading-calendar-demo-mobile.png',fullPage:true});
await page.getByRole('button',{name:'Предыдущий месяц',exact:true}).click();assert.match(await page.locator('.calendar-month').innerText(),/октябрь/i);
await page.getByRole('button',{name:'Текущий месяц',exact:true}).click();assert.match(await page.locator('.calendar-month').innerText(),/ноябрь/i);
assert.equal(writes,0);assert.deepEqual(errors,[]);console.log('Versioned interface, no Grok, live calendar, profit/loss selection, month navigation and mobile width verified; no mutations.');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});

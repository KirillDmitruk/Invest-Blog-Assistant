const assert=require('node:assert/strict'),path=require('node:path');
let chromium;try{({chromium}=require('playwright'))}catch{({chromium}=require(path.resolve(path.dirname(process.execPath),'..','node_modules','playwright')))}
(async()=>{const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--proxy-server=direct://','--proxy-bypass-list=*']});
try{const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];let writes=0;
page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')writes++});
await page.route('https://cdn.jsdelivr.net/**',r=>r.abort());
for(const [hash,button,freshness,feedback,api] of [['paper','paperRefresh','paperFreshness','paperRefreshFeedback','paper'],['trading','labRefresh','labFreshness','labFeedback','trading']]){
await page.goto('http://127.0.0.1:8000/#'+hash);
await page.locator('#'+freshness).getByText('Последний расчёт',{exact:true}).waitFor();
const wait=page.waitForResponse(r=>r.url().includes('/api/'+api+'/overview?refresh=true'));
await page.locator('#'+button).click();const response=await wait;assert.equal(response.status(),200);
await page.locator('#'+button).filter({hasText:/Обновить снимок/}).waitFor();
assert.match(await page.locator('#'+feedback).innerText(),/Облако проверено|Получен новый/);
assert.equal(await page.locator('#'+freshness+' strong').count(),3);
await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
await page.screenshot({path:'.paper-ui-check/'+hash+'-freshness-mobile.png',fullPage:true});
await page.route('**/api/'+api+'/overview?refresh=true*',r=>r.fulfill({status:503,contentType:'application/json',body:'{"detail":"Test unavailable"}'}));
await page.locator('#'+button).click();await page.locator('#'+feedback).filter({hasText:/Не удалось/}).waitFor();
assert.equal(await page.locator('#'+button).isDisabled(),false);
await page.unroute('**/api/'+api+'/overview?refresh=true*');await page.setViewportSize({width:1440,height:1000});
}
assert.equal(writes,0);assert.deepEqual(errors,[]);console.log('Both refresh buttons: uncached GET, visible feedback, timestamps, mobile layout and error recovery verified; no mutations.');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});

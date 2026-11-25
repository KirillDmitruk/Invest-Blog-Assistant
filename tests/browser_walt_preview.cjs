const assert=require('node:assert/strict');
const {chromium}=require(process.env.CONTENT_HUB_PLAYWRIGHT_PATH||'playwright');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CONTENT_HUB_BROWSER,headless:true,args:['--proxy-server=direct://','--proxy-bypass-list=*']});try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});let executeCalls=0,dialogText='';const errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().endsWith('/execute'))executeCalls++});
 page.on('dialog',d=>{dialogText=d.message();return d.dismiss()});
 await page.route('https://cdn.jsdelivr.net/**',r=>r.abort());
 await page.goto('http://127.0.0.1:8767/#walt-agent');
 await page.getByRole('button',{name:'Рассчитать лонг',exact:true}).click();
 const draft=page.locator('#wtDraft');await draft.getByRole('button',{name:'Проверить сделку в Walt'}).click();
 await draft.getByText('Граница защиты · оценка хаба',{exact:true}).waitFor();
 assert.equal(await draft.locator('.wt-consent').textContent().then(t=>t.includes('оценка хаба, не подтверждённая цена Walt')),true);
 const execute=draft.getByRole('button',{name:'Открыть реальную позицию в Walt'});
 assert.equal(await execute.isEnabled(),false);await draft.getByRole('checkbox').check();await execute.click();
 assert.equal(dialogText.includes('оценка хаба, а не цена Walt'),true);assert.equal(executeCalls,0);
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);console.log('Native preview shape passed. Local estimate explicitly labelled in review and consent. No execute request.');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});

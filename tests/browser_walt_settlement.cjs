const assert=require('node:assert/strict');
const {chromium}=require(process.env.CONTENT_HUB_PLAYWRIGHT_PATH||'playwright');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CONTENT_HUB_BROWSER,headless:true,args:['--proxy-server=direct://','--proxy-bypass-list=*']});try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});let sends=0;const errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().endsWith('/execute'))sends++});page.on('dialog',d=>d.accept());
 await page.route('https://cdn.jsdelivr.net/**',r=>r.abort());await page.goto('http://127.0.0.1:8767/#walt-agent');
 await page.getByRole('button',{name:'Рассчитать лонг',exact:true}).click();const draft=page.locator('#wtDraft');
 await draft.getByRole('button',{name:'Проверить сделку в Walt'}).click();await draft.getByRole('checkbox').check();
 await draft.getByRole('button',{name:'Открыть реальную позицию в Walt'}).click();
 await draft.getByText(/Заявка принята Walt и ещё обрабатывается/).waitFor();
 await draft.getByText(/Фактическая позиция Walt/).waitFor({timeout:20000});
 assert.equal(await draft.locator('.wt-draft-metrics').getByText('Ликвидация Walt',{exact:true}).count(),1);
 assert.equal(await draft.getByText(/Walt не вернул цену ликвидации в предпросмотре/).count(),0);
 assert.equal(await draft.locator('.wt-draft-metrics').getByText('Фактический вход',{exact:true}).count(),1);
 assert.equal(await page.locator('#wtExecute').count(),0);assert.equal(sends,1);
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);console.log('Delayed fill automatically reconciled using reads only; actual position replaced preview. One mocked execute.');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});

const assert=require('node:assert/strict');
const {chromium}=require(process.env.CONTENT_HUB_PLAYWRIGHT_PATH||'playwright');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CONTENT_HUB_BROWSER,headless:true,args:['--proxy-server=direct://','--proxy-bypass-list=*']});try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});let sends=0;const errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().endsWith('/execute'))sends++});page.on('dialog',d=>d.accept());
 await page.route('https://cdn.jsdelivr.net/**',r=>r.abort());await page.goto('http://127.0.0.1:8767/#walt-agent');
 await page.locator('#wtHistoryDetails>summary').click();await page.locator('#wtHistory button').first().click();
 const draft=page.locator('#wtDraft');await draft.getByText(/Диагностика отправки: send/).waitFor();
 await draft.getByRole('button',{name:'Проверить позиции, заявки и защитные уровни'}).click();
 await draft.getByText('Заявок не найдено. Отсутствие записи само по себе не подтверждает причину сбоя.').waitFor();
 await draft.getByRole('button',{name:'Я проверил Walt — позиции и заявки нет'}).click();
 await draft.getByText('Проверка завершена · сделки нет',{exact:true}).waitFor();
 assert.equal(await page.locator('#wtExecute').count(),0);assert.equal(sends,0);
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);console.log('Uncertain-send diagnostics and explicit absence recovery passed. No execute request.');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});

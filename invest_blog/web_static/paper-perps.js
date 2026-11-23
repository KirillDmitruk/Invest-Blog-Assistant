/* Read-only cloud PAPER dashboard. Never calls wallet or order endpoints. */
(()=>{
 const page=txt('section','');page.id='page-paper';page.className='section';
 page.innerHTML=`<div class="paper-hero"><span class="paper-badge">🧪 PAPER · АВТОНОМНО</span><h2>Торговая лаборатория</h2><p>10 000 виртуальных долларов. Реальные рыночные данные, самостоятельные решения, ни одной реальной заявки.</p><button id="paperRefresh" type="button">↻ Обновить снимок</button><p id="paperStatus" role="status">Получаем виртуальный счёт…</p></div><div id="paperStats" class="paper-stats"></div><div class="paper-card"><h2>Открытые позиции</h2><div id="paperPositions"></div></div><div class="paper-card"><h2>Последние закрытия</h2><div id="paperClosed"></div></div><details class="paper-card"><summary>Как проходит эксперимент</summary><p>Новый анализ рынка — примерно каждые 2 часа. Облачная проверка позиций — примерно каждые 15 минут; возможны задержки GitHub. Стопы и тейки восстанавливаются по закрытым минутным свечам mark price. При касании обоих уровней сначала засчитывается стоп.</p><p>Исполнение моделируется: комиссия 0,07% на вход и выход, проскальзывание 0,05%, исторический фандинг. Это не точная копия исполнения Walt. Первая неполная минута входа не переигрывается. При пробелах данных новые входы приостанавливаются.</p><p>Лимиты: риск до 1% капитала на сделку, до 4 позиций, до 2 в приблизительной группе, плечо до 3×. Дневной убыток 3% или просадка 10% приостанавливают новые входы. Подтверждённых сигналов может не быть — сделки ради активности не создаются.</p><p>Ранее открытые реальные позиции Walt не управляются этим симулятором. Проверь их самостоятельно в кошельке.</p></details>`;
 (el('page-walt-agent')||el('page-trading')||el('page-overview')).after(page);
 const button=txt('button','🧪 Автотрейдинг · демо');button.dataset.page='paper';button.onclick=()=>{showPage('paper');refresh()};document.querySelector('.nav').append(button);
 for(const id of ['page-walt-agent']){const old=el(id);if(!old)continue;const box=txt('div','');box.className='paper-card';box.append(txt('strong','🧪 Криптосимулятор: 10 000 виртуальных долларов'),txt('p','Автоматические виртуальные позиции находятся в отдельном окне. Рублёвый счёт фьючерсов Т-Инвестиций находится во вкладке «Торговый бот».'));const link=txt('button','Открыть виртуальные позиции');link.type='button';link.onclick=()=>showPage('paper');box.append(link);old.prepend(box)}
 const css=document.createElement('style');css.textContent=`.paper-hero{padding:28px;border:1px solid #4974a3;border-radius:22px;background:radial-gradient(at 95% 0%,#3e2977,transparent 60%),linear-gradient(125deg,#153949,#152333);margin-bottom:18px}.paper-hero h2{font-size:30px;margin:12px 0}.paper-hero p{color:#b5cadf;line-height:1.6}.paper-badge{color:#8ff0d1;font-size:12px;font-weight:800;letter-spacing:2px}.paper-hero button{border:1px solid #68bcd7;background:#214963;color:#e7f6ff;border-radius:10px;padding:11px 18px;cursor:pointer}.paper-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:20px}.paper-stat,.paper-card{padding:20px;border:1px solid #354c69;border-radius:17px;background:#142334;margin-bottom:15px}.paper-stat strong{display:block;font-size:26px;margin-top:10px}.paper-stat span,.paper-muted{color:#9ab4cc}.paper-position{border:1px solid #324f65;border-radius:14px;padding:18px;margin:12px 0;background:linear-gradient(120deg,#153242,#22263f)}.paper-position h3{margin:0 0 12px}.paper-fields{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.paper-fields strong{display:block;margin-top:4px}.paper-positive{color:#64e4b7}.paper-negative{color:#ff8f9a}.paper-card p{line-height:1.6}.paper-card summary{cursor:pointer;color:#87c8ff}@media(max-width:800px){.paper-stats,.paper-fields{grid-template-columns:repeat(2,1fr)}}@media(max-width:450px){.paper-stats{grid-template-columns:1fr}.paper-hero h2{font-size:25px}}`;
 document.head.append(css);
 const usd=x=>Number(x).toLocaleString('ru-RU',{style:'currency',currency:'USD',maximumFractionDigits:2});
 const price=x=>Number(x).toLocaleString('ru-RU',{maximumFractionDigits:8});
 function field(label,value){const d=txt('div','');d.append(txt('span',label),txt('strong',value));return d}
 async function refresh(){
  const b=el('paperRefresh');b.disabled=true;
  try{
   const data=await getJson('/api/paper/overview');
   if(data.real_execution_disabled){const foot=document.querySelector('.sidebar-foot');if(foot)foot.textContent='Режим PAPER. Реальные торговые действия Walt отключены.'}
   if(!data.state){el('paperStatus').textContent=data.message;return}
   const s=data.state;el('pageTitle').textContent='Автотрейдинг · демо';el('pageDesc').textContent='Облачный виртуальный счёт · без реальных заявок';
   el('paperStatus').textContent=(data.message?data.message+' ':'')+(s.last_cycle?'Проверено '+new Date(s.last_cycle*1000).toLocaleString('ru-RU')+'. ':'Первый цикл ещё не завершён. ')+s.reason;
   const stats=el('paperStats');stats.replaceChildren();
   for(const [label,value] of [['Капитал',usd(s.equity)],['Результат от старта',usd(Number(s.equity)-10000)],['Свободно',usd(s.free??10000)],['Позиции',Object.keys(s.positions).length+' / 4']]){const d=txt('div','');d.className='paper-stat';d.append(txt('span',label),txt('strong',value));stats.append(d)}
   const positions=el('paperPositions');positions.replaceChildren();
   for(const p of Object.values(s.positions)){
    const pnl=Number(p.quantity)*(Number(p.mark)-Number(p.entry))*(p.side==='long'?1:-1);
    const card=txt('article','');card.className='paper-position';card.append(txt('h3',p.symbol+' · '+(p.side==='long'?'ЛОНГ':'ШОРТ')+' · '+p.leverage+'×'));
    const fields=txt('div','');fields.className='paper-fields';for(const [label,value] of [['Маржа',usd(p.margin)],['Вход',price(p.entry)],['Текущая оценка',price(p.mark)],['PnL по цене',usd(pnl)],['Стоп',price(p.stop)],['Тейк',price(p.take)],['Фандинг списано',usd(p.funding_paid)],['Открыта',new Date(p.opened_at*1000).toLocaleString('ru-RU')]])fields.append(field(label,value));
    card.append(fields);if(p.data_error)card.append(txt('p',p.data_error));positions.append(card)
   }
   if(!Object.keys(s.positions).length)positions.append(txt('p','Пока открытых виртуальных позиций нет. '+s.reason));
   const reasons={stop:'стоп-лосс',stop_quote:'стоп по котировке',take:'тейк-профит',take_quote:'тейк по котировке',liquidation_model:'ликвидация в модели'};
   const closed=el('paperClosed');closed.replaceChildren();for(const p of [...s.closed].reverse().slice(0,10)){const row=txt('p',p.symbol+' · '+(p.side==='long'?'лонг':'шорт')+' · '+(reasons[p.reason]||p.reason)+' · '+usd(p.net_pnl));row.className=Number(p.net_pnl)>=0?'paper-positive':'paper-negative';closed.append(row)}if(!s.closed.length)closed.append(txt('p','Закрытых виртуальных сделок пока нет.'));
  }catch(e){el('paperStatus').textContent='Не удалось обновить снимок: '+e.message}finally{b.disabled=false}
 }
 el('paperRefresh').onclick=refresh;
 document.addEventListener('workspacechange',()=>{if(state.page==='paper')refresh()});
 setInterval(()=>{if(state.page==='paper'&&!document.hidden)refresh()},120000);
})();

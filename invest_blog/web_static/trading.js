/* Futures PAPER account: all displayed trades come from the persisted virtual ledger. */
(() => {
  const page=txt('section','');page.id='page-trading';page.className='section';
  const freshnessStyle=document.createElement('link');freshnessStyle.rel='stylesheet';freshnessStyle.href='/static/demo-freshness.css';document.head.append(freshnessStyle);
  const visibilityStyle=document.createElement('style');visibilityStyle.textContent='#page-trading [hidden]{display:none!important}';document.head.append(visibilityStyle);
  page.innerHTML=`
  <div class="lab-hero"><div class="lab-hero-top"><span class="lab-badge">◈ ПИЛОТ · Виртуальные фьючерсы</span><span class="lab-pill" id="labMode">Новые входы на паузе</span></div>
    <h2 id="labEquity">Загрузка счёта…</h2><strong id="labPnl" class="lab-flat">История ещё не накоплена</strong><p class="lab-source" id="labAsOf">Реальные деньги и ИИС не используются.</p>
    <div class="lab-stats"><div class="lab-stat"><span>Свободно после ГО</span><strong id="labFree">—</strong></div><div class="lab-stat"><span>ГО с запасом</span><strong id="labMargin">—</strong></div><div class="lab-stat"><span>Открытые позиции</span><strong id="labCount">0</strong></div><div class="lab-stat"><span>Просадка от пика</span><strong id="labDrawdown">0%</strong></div></div>
    <div class="editor-buttons"><button type="button" id="labCycle">Рассчитать виртуальный цикл</button><button type="button" id="labPause" class="lab-pause">Пауза новых входов</button><button type="button" id="labRefresh">Обновить экран</button></div><div id="labFreshness" class="demo-freshness"></div><div id="labFeedback" role="status" aria-live="polite"></div>
  </div>
  <div class="lab-tabs" role="tablist" aria-label="Торговая лаборатория"><button type="button" role="tab" aria-selected="true" data-lab-tab="account" class="active">Счёт и позиции</button><button type="button" role="tab" aria-selected="false" data-lab-tab="contracts">Контракты</button><button type="button" role="tab" aria-selected="false" data-lab-tab="settings">Риск и ИИ</button><button type="button" role="tab" aria-selected="false" data-lab-tab="journal">Журнал</button></div>
  <div class="lab-panel" data-lab-panel="account"><div class="grid"><div class="card full"><div class="lab-heading"><h2>Открытые позиции</h2><span class="lab-pill">Только симуляция</span></div><div id="labPositions"></div></div>
    <div class="card full"><h2>Динамика виртуального счёта</h2><div id="labChart"></div><p class="lab-source">Фиолетовая линия — сохранённая оценка, пунктир — стартовый бюджет. Данные индекса и денежного рынка не подставляются.</p></div>
    <div class="card full"><div class="lab-heading"><h2>История сделок</h2><button type="button" class="editor-btn" id="labHistoryRefresh">Обновить историю</button><label class="lab-history-filter">Показать<select class="editor-input" id="labHistoryFilter"><option value="all">Все операции</option><option value="close">Закрытия</option><option value="open">Открытия</option></select></label></div><p id="labHistoryMeta" class="lab-source"></p><div id="labClosed"></div><button type="button" id="labHistoryExpand" class="editor-btn" hidden>Показать больше</button><p class="lab-source">Последние открытия и закрытия виртуальных позиций (до 50 записей). Результат закрытия — после комиссий входа и выхода. Процент — от ГО с запасом на входе; «% —» означает, что историческое ГО не сохранено.</p></div></div></div>
  <div class="lab-panel" data-lab-panel="contracts" hidden><div class="grid"><div class="card half"><h2>Найти контракт в Т-Инвестициях</h2><form id="labSearchForm"><label class="editor-label">Тикер или название конкретного фьючерса<input id="labSearchQuery" class="editor-input" minlength="2" maxlength="60" required placeholder="Введите тикер или название"></label><div class="editor-buttons"><button type="submit">Найти фьючерсы</button></div></form><div id="labSearchResults"></div><p class="lab-source">Поиск использует существующий read-only токен. При добавлении загружаются точный UID, экспирация, шаг цены, стоимость шага и ГО. Автоматического переноса на следующую серию нет.</p></div>
    <div class="card half"><h2>Наблюдаемые контракты</h2><div id="labWatchlist"></div><p class="lab-source">До пяти контрактов. Счёт 10 000 ₽ не означает, что любой фьючерс доступен: даже один контракт может не пройти ограничение ГО или риска.</p></div></div></div>
  <div class="lab-panel" data-lab-panel="settings" hidden><div class="card full"><h2>Подключения</h2><div class="lab-connection"><div><strong>Т-Инвестиции</strong><small>Только рыночные данные: свечи, цена, торговый статус и параметры контракта.</small><span class="lab-pill">Заявки недоступны</span></div><div><strong id="labAiTitle">ИИ · анализ рынка</strong><small id="labAiModel">Автоматическая проверка рыночных данных.</small></div><div><strong>Фоновый расчёт</strong><small id="labHeartbeat">Процесс ещё не отмечался.</small><code>.\\hub trade-start</code></div></div>
    </div><div class="card full" style="margin-top:16px"><h2>Бюджет и ограничения</h2><form id="labSettingsForm"><div class="lab-risk-grid">
      <label>Виртуальный старт, ₽<input class="editor-input" id="labBudget" inputmode="decimal" required></label><label>Источник решений<select class="editor-input" id="labStrategy"><option value="ai">ИИ · анализ рынка</option><option value="rules">Базовые правила — без ИИ</option></select></label><label>Новые виртуальные входы<select class="editor-input" id="labEnabled"><option value="false">Пауза</option><option value="true">Разрешены в симуляторе</option></select></label>
      <label>Риск на сделку, %<input class="editor-input" id="labRisk" inputmode="decimal" required></label><label>Предел потерь за день, %<input class="editor-input" id="labDaily" inputmode="decimal" required></label><label>Предел просадки от пика, %<input class="editor-input" id="labMaxDrawdown" inputmode="decimal" required></label>
      <label>Максимум ГО от счёта, %<input class="editor-input" id="labMaxMargin" inputmode="decimal" required></label><label>Максимум позиций<input class="editor-input" type="number" id="labMaxPositions" min="1" max="5" required></label><label>Максимум контрактов во входе<input class="editor-input" type="number" id="labMaxContracts" min="1" max="10" required></label>
      <label>Комиссия за контракт и сторону, ₽<input class="editor-input" id="labCommission" inputmode="decimal" required></label><label>Проскальзывание, тиков<input class="editor-input" type="number" id="labSlippage" min="0" max="20" required></label><label>Запас к биржевому ГО, %<input class="editor-input" id="labMarginBuffer" inputmode="decimal" required></label></div><div class="editor-buttons"><button type="submit" id="labSave">Сохранить настройки</button><button type="button" id="labSettingsReset">Отменить изменения</button></div></form><p class="lab-source">Комиссия по умолчанию 2 ₽ — допущение симуляции, не твой тариф брокера. Измени её по своему тарифу. Риск ограничивает размер позиции, но гэп может привести к большей потере. После первой сделки стартовый бюджет не меняется.</p></div>
    <details class="card full" style="margin-top:16px"><summary>Что учитывает эта версия</summary><div id="labAssumptions"></div><p class="lab-quiet">PnL считается в рублях через шаг цены и стоимость шага, с накоплением изменений между наблюдениями. Стопы и цели проверяются только на свежих доступных котировках, исполнение — по наблюдаемой цене с проскальзыванием. Не моделируются полный клиринг, поставка, персональное брокерское ГО, налоги и гарантированная ликвидность.</p></details>
  </div>
  <div class="lab-panel" data-lab-panel="journal" hidden><div class="grid"><div class="card full"><h2>Последние решения</h2><div id="labDecisions"></div><p class="lab-source">Самооценка модели не является вероятностью прибыли. Отсутствие сделок при сработавших лимитах — штатный результат.</p></div><div class="card full"><h2>Виртуальные действия</h2><div class="scroll" id="labTrades"></div></div><div class="card full"><h2>Работа и расписание</h2><div id="labRuns"></div><p class="lab-source">Включённый фоновый процесс рассчитывает цикл каждые 5 минут, а решение запрашивается не чаще одного раза на завершённую 15-минутную свечу контракта. При паузе новые входы не открываются; проверка уже существующих виртуальных стопов продолжается.</p></div></div></div>`;
  const accountSplit=txt('div','');accountSplit.className='full demo-account-split';
  const positionsPanel=page.querySelector('#labPositions').closest('.card'),chartPanel=page.querySelector('#labChart').closest('.card');
  chartPanel.classList.add('demo-chart-panel');positionsPanel.before(accountSplit);accountSplit.append(positionsPanel,chartPanel);
  const calendarHost=txt('div','');calendarHost.id='labCalendar';calendarHost.className='card full';calendarHost.style.marginTop='20px';
  const statistics=txt('div','');statistics.className='lab-panel';statistics.dataset.labPanel='statistics';statistics.hidden=true;statistics.append(calendarHost);page.append(statistics);
  const statisticsTab=txt('button','Статистика');statisticsTab.type='button';statisticsTab.dataset.labTab='statistics';statisticsTab.setAttribute('role','tab');statisticsTab.setAttribute('aria-selected','false');page.querySelector('.lab-tabs').insertBefore(statisticsTab,page.querySelector('[data-lab-tab="contracts"]'));
  const calendar=new window.DemoCalendar(calendarHost);
  document.querySelector('main footer').before(page);
  const nav=txt('button','◈ Т-инвестиции демо');nav.type='button';nav.dataset.page='trading';nav.onclick=()=>showPage('trading');document.querySelector('.nav').append(nav);
  let overview=null,formRevision=1,formSaved='',requestId=0;
  const inputs=['labBudget','labStrategy','labEnabled','labRisk','labDaily','labMaxDrawdown','labMaxMargin','labMaxPositions','labMaxContracts','labCommission','labSlippage','labMarginBuffer'];
  const formSnapshot=()=>JSON.stringify(inputs.map(id=>el(id).value));
  const stamp=v=>v?new Date(v).toLocaleString('ru-RU'):'—';
  const n=v=>Number(v||0);
  const price=v=>n(v).toLocaleString('ru-RU',{maximumFractionDigits:8});
  const signed=v=>(n(v)>0?'+':'')+rub(n(v));
  const polarity=(node,v)=>{node.classList.remove('lab-positive','lab-negative','lab-flat');node.classList.add(n(v)>0?'lab-positive':n(v)<0?'lab-negative':'lab-flat')};
  const message=(text,bad=false)=>el('labFeedback').replaceChildren(alertNode(text,bad?'warning':'info'));
  async function request(path,method='POST',body={}){
    if(!state.editorToken)state.editorToken=(await getJson('/api/editor-token')).token;
    const response=await fetch('/api/trading'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':state.editorToken},body:JSON.stringify(body)});
    const result=await response.json().catch(()=>({}));if(!response.ok){const d=result.detail;throw Error(Array.isArray(d)?d.map(x=>x.loc.slice(1).join('.')+': '+x.msg).join('; '):d||'Не удалось выполнить действие')}
    return result;
  }
  function selectTab(name){page.querySelectorAll('[data-lab-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.labTab===name);b.setAttribute('aria-selected',String(b.dataset.labTab===name))});page.querySelectorAll('[data-lab-panel]').forEach(p=>p.hidden=p.dataset.labPanel!==name)}
  page.querySelectorAll('[data-lab-tab]').forEach(b=>b.onclick=()=>selectTab(b.dataset.labTab));
  function populateForm(){if(!overview)return;const s=overview.state,r=s.risk;const values=[s.budget,s.strategy,String(s.enabled),r.risk_pct,r.daily_loss_pct,r.max_drawdown_pct,r.max_margin_pct,r.max_positions,r.max_contracts,r.commission,r.slippage_ticks,r.margin_buffer_pct];inputs.forEach((id,i)=>el(id).value=values[i]);formRevision=s.config_revision;formSaved=formSnapshot()}
  function renderChart(){window.DemoEquityChart.draw(el('labChart'),overview.history,{budget:overview.state.budget,currency:'RUB',label:'История виртуального капитала Т-Инвестиций в рублях'})}
  let historyExpanded=false;
  function renderTradeHistory(){
    if(!overview)return;const chronological=[...overview.trades].sort((a,b)=>a.id-b.id),pending=new Map(),settled=new Map();
    const amount=x=>x!=null&&Number.isFinite(Number(x))?Number(x):null;
    for(const t of chronological){
      if(['open_long','open_short'].includes(t.action)){pending.set(t.uid,t);continue}
      if(t.action!=='close')continue;const entry=pending.get(t.uid);pending.delete(t.uid);
      const gross=amount(t.detail.pnl_before_exit_fee),exitFee=amount(t.detail.fee),entryFee=amount(t.detail.entry_fee)??(entry?amount(entry.detail.fee):null);
      const net=gross!==null&&exitFee!==null&&entryFee!==null?gross-exitFee-entryFee:null;
      const margin=amount(t.detail.entry_margin)??(entry?amount(entry.detail.entry_margin):null);
      settled.set(t.id,{net,roi:net!==null&&margin!==null&&margin>0?net/margin*100:null});
    }
    const filter=el('labHistoryFilter').value;
    const rows=[...chronological].reverse().filter(t=>['open_long','open_short','close'].includes(t.action)&&(filter==='all'||filter==='close'&&t.action==='close'||filter==='open'&&t.action!=='close'));
    const shown=historyExpanded?rows:rows.slice(0,10),closed=el('labClosed');closed.replaceChildren();
    const time=v=>new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})+' МСК';
    for(const t of shown){
      const row=txt('div','');row.className='paper-closed-row';row.dataset.tradeId=t.id;row.dataset.tradeAction=t.action;
      const side=({long:'лонг',short:'шорт'}[t.detail.side]||({open_long:'лонг',open_short:'шорт'}[t.action]))||'направление не указано';
      if(t.action==='close'){
        const {net,roi}=settled.get(t.id);row.classList.add(net===null?'lab-flat':net>=0?'paper-positive':'paper-negative');
        row.append(txt('strong','Закрытие · '+t.ticker+' · '+side+' · '+(t.detail.reason||'закрытие')+' · '+(net===null?'результат не подтверждён':signed(net))));
        const percentage=txt('span',roi===null?'% —':(roi>0?'+':'')+roi.toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2})+'%');percentage.className='paper-closed-roi';percentage.title=roi===null?'ГО на момент входа не сохранено; процент не рассчитывается.':'Чистый результат после комиссий / ГО с запасом на входе × 100%';row.append(percentage);
        if(net===null)row.append(txt('p','Нет полного набора комиссий в доступном журнале.'));
      }else{
        row.classList.add('lab-flat');const current=overview.state.positions[t.uid];
        const active=current&&Date.parse(current.opened_at)===Date.parse(t.at);
        row.append(txt('strong','Открытие · '+t.ticker+' · '+side+' · '+t.detail.contracts+' контр. · '+price(t.detail.price)+' пт.'));
        if(active){const tag=txt('span','Позиция открыта');tag.className='lab-pill lab-history-open';row.append(tag)}
        const fee=amount(t.detail.fee);row.append(txt('p',fee===null?'Комиссия входа не подтверждена.':'Комиссия входа: '+rub(fee)+'. Итог сделки появится в записи закрытия.'));
      }
      const date=txt('small',time(t.at));date.style.display='block';date.className='lab-source';row.append(date);closed.append(row);
    }
    if(!rows.length)closed.append(txt('p',filter==='close'?'Закрытых виртуальных сделок пока нет.':'Операций по выбранному фильтру нет.'));
    el('labHistoryMeta').textContent='Показано '+shown.length+' из '+rows.length+' операций'+(rows.length?' · последняя: '+time(rows[0].at):'')+(overview.state.last_cycle?' · облачный снимок: '+time(overview.state.last_cycle):'')+'.';
    const expand=el('labHistoryExpand');expand.hidden=rows.length<=10;expand.textContent=historyExpanded?'Свернуть до 10 операций':'Показать все '+rows.length+' операций';
  }
  el('labHistoryRefresh').onclick=()=>refresh(true);
  el('labHistoryFilter').onchange=()=>{historyExpanded=false;renderTradeHistory()};
  el('labHistoryExpand').onclick=()=>{historyExpanded=!historyExpanded;renderTradeHistory()};
  function render(){const {state:s,account:a,ai}=overview;calendar.update(overview.daily_results||[]);
    el('labCycle').textContent=s.strategy==='connector'?'Проверить позиции и стопы':'Рассчитать виртуальный цикл';
    el('labEquity').textContent=rub(n(a.equity));el('labPnl').textContent=signed(a.net_pnl)+' · '+(100*n(a.net_pnl)/n(s.budget)).toFixed(2)+'% от виртуального старта';polarity(el('labPnl'),a.net_pnl);
    el('labFree').textContent=rub(n(a.free_margin));el('labMargin').textContent=rub(n(a.margin));el('labCount').textContent=Object.keys(s.positions).length+' / '+s.risk.max_positions;el('labDrawdown').textContent=n(a.drawdown_pct).toFixed(2)+'%';
    el('labMode').textContent=s.enabled?'Входы разрешены · симуляция':'Новые входы на паузе';el('labAsOf').textContent='Последний цикл: '+stamp(s.last_cycle)+' · '+(s.strategy==='connector'?'ИИ':s.strategy==='ai'?'Решения ИИ':'Базовые правила, без ИИ')+' · реальные заявки недоступны';
    const positions=el('labPositions');positions.replaceChildren();
    if(!Object.keys(s.positions).length)positions.append(txt('p',overview.cloud_mode?'Пока нет виртуальных позиций. Бот ждёт подходящий сигнал и проверяет лимиты; ручное подтверждение не требуется.':'Открытых виртуальных позиций нет. Выбери контракт и настрой режим в разделе «Риск и ИИ».'));
    for(const [uid,p] of Object.entries(s.positions)){
      const card=txt('article','');card.className='paper-position '+(p.side==='long'?'paper-long':'paper-short');
      const head=txt('div','');head.className='paper-position-head';const title=txt('div','');title.append(txt('h3',p.ticker));
      const badge=txt('span',(p.side==='long'?'↗ Лонг':'↘ Шорт')+' · '+p.contracts+' контр.');badge.className='paper-direction';title.append(badge);
      const result=txt('strong',signed(p.accrued_pnl));result.className='paper-position-pnl '+(n(p.accrued_pnl)>=0?'paper-positive':'paper-negative');const resultBox=txt('div','');resultBox.className='paper-position-result';
    const roi=Number.isFinite(Number(p.accrued_pnl))&&Number.isFinite(Number(p.reserved_margin))&&Number(p.reserved_margin)>0?Number(p.accrued_pnl)/Number(p.reserved_margin)*100:null;
    const percent=txt('span',roi===null?'—':(roi>0?'+':'')+roi.toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2})+'%');percent.className='paper-position-roi '+(Number(p.accrued_pnl)>=0?'paper-positive':'paper-negative');percent.title='Текущий PnL до комиссий / ГО с запасом × 100%. Это доходность на обеспечение, а не изменение цены фьючерса.';
    const roiNote=txt('small','от ГО · до комиссий');roiNote.className='paper-roi-note';resultBox.append(result,percent,roiNote);head.append(title,resultBox);card.append(head);
      const fields=txt('div','');fields.className='paper-fields';
      for(const [label,value] of [['ГО с запасом',rub(n(p.reserved_margin))],['Контрактов',p.contracts],['Вход',price(p.entry)+' пт.'],['Текущая оценка',price(p.last_price)+' пт.'],['Стоп',price(p.stop)+' пт.'],['Тейк',price(p.target)+' пт.'],['Открыта',stamp(p.opened_at)],['Котировка',stamp(p.quoted_at)]]){
       const f=txt('div','');f.append(txt('span',label),txt('strong',value));if(label==='Стоп')f.className='paper-stop';if(label==='Тейк')f.className='paper-take';fields.append(f)
      }card.append(fields);
      const distance=n(p.last_price)>0?(n(p.last_price)-n(p.stop))*(p.side==='long'?1:-1)/n(p.last_price)*100:null;
      const detail=txt('p','PnL до комиссий · '+(distance===null?'Расстояние до стопа не определено':distance>0?'до стопа '+distance.toFixed(2)+'%':'уровень стопа достигнут')+' · экспозиция '+rub(n(p.last_price)/n(p.tick)*n(p.tick_value)*p.contracts));detail.className='lab-quiet';card.append(detail);
      const actions=txt('div','');actions.className='editor-buttons';const close=txt('button','Закрыть виртуально');close.type='button';close.onclick=async()=>{if(!confirm('Закрыть виртуальную позицию '+p.ticker+' по свежей котировке с проскальзыванием?'))return;close.disabled=true;try{await request('/positions/'+encodeURIComponent(uid)+'/close','POST',{confirm:'PAPER_CLOSE'});await refresh();message('Виртуальная позиция закрыта.')}catch(e){message(e.message,true)}finally{close.disabled=false}};if(!overview.cloud_mode)actions.append(close);card.append(actions);positions.append(card);
    }
    renderChart();const decisions=el('labDecisions');decisions.replaceChildren();if(!overview.decisions.length)decisions.append(txt('p','Решений ещё нет. Объяснения появятся после анализа свежих рыночных данных.'));
    for(const row of overview.decisions.slice(0,6)){const d=row.detail,card=txt('div','');card.className='lab-decision';card.append(txt('strong',d.ticker+' · '+({long:'Лонг',short:'Шорт',close:'Закрыть',hold:'Пропуск'}[d.action]||d.action)),txt('p',d.reason||'Решение зарезервировано.'));if(d.result){const result=txt('p',d.result);result.className='lab-result';card.append(result)}card.append(txt('small',stamp(row.at)+' · '+(d.model||(overview.cloud_mode?overview.state.cloud.model:'запрос'))+(d.strategy==='ai'&&d.confidence!==undefined?' · самооценка '+Math.round(d.confidence*100)+'%':'')));decisions.append(card)}
    const watch=el('labWatchlist');watch.replaceChildren();if(!Object.keys(s.watchlist).length)watch.append(txt('p','Контракты ещё не добавлены.'));
    for(const [uid,c] of Object.entries(s.watchlist)){const card=txt('div','');card.className='lab-watch';const info=txt('div','');info.append(txt('strong',c.ticker+' · '+c.name),txt('p','Тик '+c.tick+' пт. / '+rub(n(c.tick_value))+' · ГО лонг '+rub(n(c.margin_long))+' / шорт '+rub(n(c.margin_short))),txt('p','Экспирация: '+stamp(c.expires_at)));if(s.marks[uid])info.append(txt('p','Котировка '+s.marks[uid].price+' пт. · '+stamp(s.marks[uid].quoted_at)));
      const remove=txt('button','Убрать');remove.type='button';remove.className='editor-btn';remove.onclick=async()=>{try{await request('/watchlist/'+encodeURIComponent(uid),'DELETE');await refresh()}catch(e){message(e.message,true)}};card.append(info);if(!overview.cloud_mode)card.append(remove);watch.append(card)}
    el('labAiTitle').textContent=(ai.provider==='xai'?'ИИ':ai.provider==='none'?'ИИ не выбран':ai.provider)+' · '+(ai.configured?'ключ указан':'не подключён');el('labAiModel').textContent=ai.model||'Модель задаётся в .env. Ключ в браузер не передаётся.';
    el('labHeartbeat').textContent=s.worker_heartbeat?'Последний сигнал: '+stamp(s.worker_heartbeat)+'. Проверка процесса: .\\hub trade-status':'Процесс ещё не отмечался. Запуск: .\\hub trade-start';
    if(!formSaved||formSnapshot()===formSaved)populateForm();
    el('labAssumptions').replaceChildren(...overview.notes.map(t=>txt('p',t)));
    renderTradeHistory();
    table('labTrades',['Когда','Контракт','Действие','Контрактов','Цена, пункты','Комиссия, ₽','PnL до комиссии выхода'],overview.trades.map(t=>[stamp(t.at),t.ticker,({open_long:'Вход в лонг',open_short:'Вход в шорт',close:'Закрытие'}[t.action]||t.action),t.detail.contracts,t.detail.price,rub(n(t.detail.fee)),t.action==='close'?signed(t.detail.pnl_before_exit_fee):'—']));
    el('labRuns').replaceChildren(...overview.runs.map(r=>{const card=txt('div','');card.className='lab-decision';card.append(txt('strong',stamp(r.at)+' · '+r.status),txt('p',r.note));return card}));
    if(overview.cloud_mode){
      el('labMode').textContent=s.enabled?'☁ Автоматически · виртуальный счёт':'☁ Пауза новых входов';
      el('labAsOf').textContent='Старт 1 000 000 ₽ · облачный цикл '+stamp(s.last_cycle)+' · '+s.cloud.ai_status+(s.strategy_baseline?' · Правила v2 с '+stamp(s.strategy_baseline.at)+' · результат с обновления '+signed(n(a.equity)-n(s.strategy_baseline.equity)):'');
      el('pageTitle').textContent='Т-инвестиции демо';el('pageDesc').textContent='Т-Инвестиции · виртуальные фьючерсы · 1 000 000 ₽ · автономно в облаке';
      for(const id of ['labCycle','labPause'])el(id).hidden=true;
      el('labSearchForm').closest('.card').hidden=true;
      for(const control of el('labSettingsForm').querySelectorAll('input,select,button'))control.disabled=true;
      const conn=el('labAiTitle').closest('.card');for(const d of conn.querySelectorAll('details,code'))d.hidden=true;
      el('labAiTitle').textContent='Cloudflare · автоматический анализ';el('labAiModel').textContent=ai.model+' · при недоступности ИИ новые входы пропускаются';
      if(s.strategy_version==='t-futures-v2-20261125')el('labAssumptions').append(txt('p','Правила v2: новые входы требуют подтверждения закрытыми часовыми свечами. После стопа по контракту — часовая пауза. Старые позиции продолжают сопровождаться; история не сбрасывается.'));
      el('labHeartbeat').textContent='Облако работает без ноутбука. Мониторинг ≈5 мин, анализ ≈2 ч. Последняя проверка: '+stamp(s.worker_heartbeat);
      const watchNote=el('labWatchlist').nextElementSibling;if(watchNote)watchNote.textContent=s.cloud.coverage?.selection||'Каталог загружается из Т-Инвестиций.';
      const runNote=el('labRuns').nextElementSibling;if(runNote)runNote.textContent='Проверки примерно раз в 5 минут, анализ ИИ раз в 2 часа. Расписание может задерживаться. Стопы и цели проверяются по минутной истории.';
      if(!overview.decisions.length)el('labDecisions').replaceChildren(txt('p','Облако ожидает свежие рыночные данные. Решения и объяснения появятся здесь.'));
      if(overview.sync==='unavailable')message('Нет связи с облаком: показан сохранённый снимок.',true);
    }
    if(s.auto_pause_reason)message(s.auto_pause_reason,true);
  }
  async function refresh(manual=false){const b=el('labRefresh');if(b.disabled)return;const id=++requestId,before=overview?.state.last_cycle;b.disabled=true;b.textContent='Получаем снимок…';el('labHistoryRefresh').disabled=true;el('labHistoryRefresh').textContent='Проверяем облако…';if(manual)message('Проверяем сохранённый результат в облаке…');try{const next=await getJson('/api/trading/overview'+(manual?'?refresh=true&t='+Date.now():''));if(id!==requestId)return;overview=next;render();
    const f=el('labFreshness'),cycle=next.state.last_cycle?new Date(next.state.last_cycle).getTime():0,age=cycle?Math.max(0,Math.floor((Date.now()-cycle)/60000)):null;f.replaceChildren();f.className='demo-freshness'+(age===null||age>10?' demo-stale':'');
    for(const [label,value] of [['Последний расчёт',stamp(next.state.last_cycle)],['Снимок получен',next.fetched_at?new Date(next.fetched_at*1000).toLocaleTimeString('ru-RU'):'—'],['Возраст данных',age===null?'Нет расчёта':age+' мин']]){const d=txt('div','');d.append(txt('span',label),txt('strong',value));f.append(d)}
    if(age===null||age>10){const warning=txt('p','⚠ Расчёт задерживается. Это сохранённая оценка, не текущая цена. Кнопка загружает снимок и не запускает торговый цикл.');warning.className='demo-stale-note';f.append(warning)}
    if(manual)message(next.sync==='unavailable'?'Облако недоступно. Показан прежний снимок.':before===next.state.last_cycle?'Облако проверено. Нового расчёта пока нет; суммы остались прежними.':'Получен новый облачный расчёт. Позиции и суммы обновлены.',next.sync==='unavailable');
  }catch(e){message('Не удалось загрузить симулятор: '+e.message,true)}finally{b.disabled=false;b.textContent='Обновить снимок';el('labHistoryRefresh').disabled=false;el('labHistoryRefresh').textContent='Обновить историю'}}
  el('labRefresh').onclick=()=>refresh(true);
  el('labCycle').onclick=async()=>{const b=el('labCycle');b.disabled=true;message('Получаем рыночные данные и рассчитываем виртуальный цикл…');try{const result=await request('/cycle');await refresh();message(result.note)}catch(e){message(e.message,true)}finally{b.disabled=false}};
  el('labPause').onclick=async()=>{try{await request('/pause');await refresh();message('Новые входы остановлены. Виртуальные позиции и история сохранены.')}catch(e){message(e.message,true)}};
  el('labSearchForm').onsubmit=async e=>{e.preventDefault();const b=el('labSearchForm').querySelector('button');b.disabled=true;try{const result=await request('/search','POST',{query:el('labSearchQuery').value});const host=el('labSearchResults');host.replaceChildren();if(!result.items.length)host.append(txt('p','Доступных фьючерсов не найдено. Попробуй точный тикер.'));for(const item of result.items){const row=txt('div','');row.className='lab-watch';row.append(txt('strong',item.ticker+' · '+item.name));const add=txt('button','Добавить');add.type='button';add.className='editor-btn';add.onclick=async()=>{add.disabled=true;try{await request('/watchlist','POST',{uid:item.uid});await refresh();message('Контракт добавлен в наблюдение. Сделка не открывалась.')}catch(error){message(error.message,true)}finally{add.disabled=false}};row.append(add);host.append(row)}}catch(error){message(error.message,true)}finally{b.disabled=false}};
  const number=id=>el(id).value.trim().replace(',','.');
  el('labSettingsForm').onsubmit=async e=>{e.preventDefault();const before=formSnapshot(),button=el('labSave');button.disabled=true;try{const body={revision:formRevision,budget:number('labBudget'),strategy:el('labStrategy').value,enabled:el('labEnabled').value==='true',risk:{risk_pct:number('labRisk'),daily_loss_pct:number('labDaily'),max_drawdown_pct:number('labMaxDrawdown'),max_margin_pct:number('labMaxMargin'),max_positions:n(el('labMaxPositions').value),max_contracts:n(el('labMaxContracts').value),commission:number('labCommission'),slippage_ticks:n(el('labSlippage').value),margin_buffer_pct:number('labMarginBuffer')}};const result=await request('/settings','PUT',body);formRevision=result.config_revision;formSaved=before;await refresh();message('Настройки сохранены. Для автоматических циклов должен работать .\\hub trade-start.')}catch(error){message(error.message,true)}finally{button.disabled=false}};
  el('labSettingsReset').onclick=()=>{if(formSnapshot()!==formSaved&&!confirm('Отменить несохранённые изменения ограничений?'))return;populateForm()};
  document.addEventListener('workspacechange',()=>{if(state.page==='trading'){el('pageDesc').textContent='Фьючерсы · виртуальный счёт · решения, ограничения и журнал';refresh()}});
  setInterval(()=>{if(state.page==='trading'&&!document.hidden)refresh()},60000);
  window.addEventListener('beforeunload',e=>{if(formSaved&&formSnapshot()!==formSaved){e.preventDefault();e.returnValue=''}});
})();

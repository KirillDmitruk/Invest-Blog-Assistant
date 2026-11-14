/* Manual Walt portfolio; valuation dates and incomplete totals remain visible. */
(() => {
  const section = (id, html) => {
    const n=document.createElement('section');n.id='page-'+id;n.className='section';n.innerHTML=html;
    document.querySelector('main footer').before(n);
    const b=txt('button',id==='assets'?'Все активы':'Walt');b.type='button';b.dataset.page=id;
    b.onclick=()=>showPage(id);document.querySelector('.nav').append(b);
    return n;
  };
  section('assets', `
    <div class="editor-toolbar"><label>Показывать <select id="assetsFilter" class="editor-input"><option value="all">ИИС / Т-Инвестиции + Walt</option><option value="tinvest">Т-Инвестиции</option><option value="walt">Walt</option></select></label><div class="editor-buttons"><button type="button" id="assetsRefresh">Обновить обзор</button><button type="button" id="assetsSnapshot">Сохранить снимок</button><button type="button" id="assetsOpenWalt">Добавить позиции Walt</button></div></div>
    <div id="assetsFeedback" role="status"></div><div id="assetsWarnings"></div>
    <div class="grid">
      <div class="card half"><div class="name">Стоимость выбранного портфеля, ₽</div><h2 id="assetsTotalRub">—</h2><p class="hint" id="assetsTotalHint"></p></div>
      <div class="card half"><div class="name">Эквивалент в USD</div><h2 id="assetsTotalUsd">—</h2><p class="hint">USD — валюта оценки. Это не означает, что все активы являются долларами.</p></div>
      <div class="card half"><h2>Т-Инвестиции</h2><div id="assetsBroker"></div><button type="button" class="editor-btn" id="assetsBrokerLink">Открыть аналитику брокера</button></div>
      <div class="card half"><h2>Walt</h2><div id="assetsWalt"></div><p class="hint">Ручной учёт количества и цен. Прямой синхронизации с Walt нет. USDT учитывается как стейблкоин с явно указанной ценой, а xStocks — как токены.</p></div>
      <div class="card full"><h2>Состав портфеля</h2><div id="assetsAllocation" class="scroll"></div><p class="hint">Доли доступны при полной оценке выбранного портфеля. Токенизированные акции и фонды выделены отдельно. Разбивка по экономической валютной экспозиции пока не рассчитывается.</p></div>
      <div class="card full"><h2>Курс для пересчёта USD → RUB</h2><form id="assetsFxForm" class="goal-form"><label>Рублей за 1 USD<input id="assetsFxRate" type="text" inputmode="decimal" required placeholder="Например, 95.50"></label><label>Дата курса<input id="assetsFxDate" type="date" required></label><div class="goal-actions"><button type="submit">Сохранить курс</button><button type="button" id="assetsFxCbr">Получить курс ЦБ</button><button type="button" id="assetsFxReset">Отменить изменения</button></div></form><p class="hint" id="assetsFxInfo"></p><p class="hint">Курс ЦБ используется для справочного пересчёта, а не как цена покупки или продажи USDT. Цены позиций он не обновляет.</p></div>
      <div class="card full"><h2>История оценок</h2><div id="assetsHistory" class="scroll"></div><p class="hint">Снимки сохраняются при изменении позиций, курса и по кнопке. Это история стоимости, а не доходности: изменения включают покупки, продажи и переводы. Общая доходность недоступна без истории операций.</p></div>
    </div>`);
  section('walt', `
    <div class="note">Внеси фактические остатки из разделов Walt «Акции и фонды» и «Крипто». Включай средства в Earn отдельной позицией только если они не входят в уже указанное количество. Логин и доступ к кошельку не требуются.</div>
    <div class="editor-buttons"><button type="button" id="waltOverview">Все активы</button><button type="button" id="waltRefresh">Обновить список</button></div>
    <div id="waltFeedback" role="status"></div>
    <div class="grid"><div class="card half"><h2 id="waltFormTitle">Новая позиция Walt</h2>
    <form id="waltForm" class="goal-form">
      <label>Точный тикер<input id="waltSymbol" maxlength="40" required placeholder="BTC, USDT, SPYx"></label>
      <label>Название<input id="waltName" maxlength="150" required placeholder="Название из Walt"></label>
      <label>Тип актива<select id="waltKind"><option value="crypto">Криптовалюта</option><option value="stablecoin">Стейблкоин</option><option value="tokenized_stock">Токенизированная акция</option><option value="tokenized_etf">Токенизированный фонд</option><option value="other">Другое</option></select></label>
      <label>Количество<input id="waltQuantity" type="text" inputmode="decimal" required placeholder="Например, 0.01234567"></label>
      <label>Цена одной единицы, USD<input id="waltPrice" type="text" inputmode="decimal" placeholder="Нет цены — оставь пустым"></label>
      <label>Дата оценки<input id="waltDate" type="date" required></label>
      <label>Всего потрачено, USD (если известно)<input id="waltCost" type="text" inputmode="decimal" placeholder="Необязательно, с учётом комиссий"></label>
      <label>Заметка<input id="waltNotes" maxlength="1500" placeholder="Источник цены, ограничения, Earn"></label>
      <div class="goal-actions"><button type="submit" id="waltSave">Сохранить</button><button type="button" id="waltReset">Новая позиция</button></div>
    </form><p class="hint">Цена — именно токена в Walt на указанную дату, не обычной акции с похожим тикером. Можно ввести запятую или точку. При отсутствии цены позиция сохраняется, но полная стоимость остаётся неизвестной.</p></div>
    <div class="card half"><h2>Позиции Walt</h2><div id="waltTotals"></div><div class="scroll" id="waltPositions"></div></div></div>`);

  let data=null, selected=null, fxRevision=0, loading=0;
  const form=el('waltForm');
  const fields=['waltSymbol','waltName','waltKind','waltQuantity','waltPrice','waltDate','waltCost','waltNotes'];
  const today=()=>new Date().toLocaleDateString('en-CA',{timeZone:'Europe/Moscow'});
  el('waltDate').value=el('assetsFxDate').value=today();
  el('waltDate').max=el('assetsFxDate').max=today();
  const snapshot=()=>JSON.stringify(fields.map(id=>el(id).value));
  let saved=snapshot();
  const fxSnapshot=()=>JSON.stringify([el('assetsFxRate').value,el('assetsFxDate').value]);
  let savedFx=fxSnapshot();
  const dirty=()=>snapshot()!==saved;
  const canReplace=()=>!dirty()||confirm('Есть несохранённые изменения позиции Walt. Заменить их?');
  const usd=v=>v===null||v===undefined?'Нет полной оценки':new Intl.NumberFormat('ru-RU',{style:'currency',currency:'USD'}).format(Number(v));
  const feedback=(id,message,bad=false)=>el(id).replaceChildren(alertNode(message,bad?'warning':'info'));
  const decimal=id=>el(id).value.trim().replace(',','.');
  const optional=id=>decimal(id)||null;
  const errorText=e=>typeof e.message==='string'?e.message:'Не удалось выполнить действие';
  async function mutate(path,method,payload){
    if(!state.editorToken)state.editorToken=(await getJson('/api/editor-token')).token;
    // Format validation errors as readable text instead of [object Object].
    const response=await fetch('/api/assets'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':state.editorToken},body:JSON.stringify(payload)});
    const result=await response.json().catch(()=>({}));
    if(!response.ok){const detail=result.detail;throw Error(Array.isArray(detail)?detail.map(x=>x.loc.slice(1).join('.')+': '+x.msg).join('; '):detail||'Ошибка сохранения')}
    return result;
  }
  function reset(){selected=null;form.reset();el('waltDate').value=today();el('waltFormTitle').textContent='Новая позиция Walt';saved=snapshot()}
  function edit(p){if(el('waltSave').disabled||!canReplace())return;selected=p;const values=[p.symbol,p.name,p.kind,p.quantity,p.unit_price_usd??'',p.valued_on,p.cost_usd??'',p.notes];fields.forEach((id,i)=>el(id).value=values[i]);saved=snapshot();el('waltFormTitle').textContent='Редактирование · '+p.symbol;form.scrollIntoView({behavior:'smooth',block:'center'})}
  function render(){
    if(!data)return;
    const filter=el('assetsFilter').value,rate=data.fx.usd_rub?Number(data.fx.usd_rub):null;
    const broker=data.broker.total_rub,walt=data.walt.total_usd;
    const totalRub=filter==='all'?data.combined.total_rub:filter==='tinvest'?broker:(walt!==null&&rate?Number(walt)*rate:null);
    const totalUsd=filter==='walt'?walt:(totalRub!==null&&rate?Number(totalRub)/rate:null);
    el('assetsTotalRub').textContent=totalRub===null?'Нет полной оценки':rub(Number(totalRub));
    el('assetsTotalUsd').textContent=usd(totalUsd);
    el('assetsTotalHint').textContent=totalRub===null?'Добавь недостающие позиции, цены, курс или снимки счетов. Доступные суммы показаны ниже.':'Сумма последних сохранённых оценок. Проверь даты источников.';
    el('assetsWarnings').replaceChildren(...data.warnings.map(x=>alertNode(x,'warning')));
    const brokerBox=el('assetsBroker');brokerBox.replaceChildren(txt('p','Известная сумма: '+rub(Number(data.broker.known_total_rub))));
    for(const a of data.broker.accounts)brokerBox.append(txt('p','Счёт '+a.account_id+' · '+rub(Number(a.total_rub))+' · '+a.day));
    if(!data.broker.accounts.length)brokerBox.append(txt('p','Сначала выполни синхронизацию Т-Инвестиций.'));
    el('assetsWalt').replaceChildren(txt('p',data.walt.has_data?'Оценённая часть: '+usd(data.walt.known_total_usd):'Остатки ещё не внесены'),txt('p','Позиций: '+data.positions.length+' · Без цены: '+data.walt.missing_prices.length));
    const rows=data.allocation.filter(r=>filter==='all'||r.source===filter).map(r=>{
      const value=r.value_rub===null||r.value_rub===undefined?null:Number(r.value_rub);
      return [r.label,value===null?'Нет курса':rub(value),r.value_usd?usd(r.value_usd):'—',totalRub!==null&&Number(totalRub)>0&&value!==null?(100*value/Number(totalRub)).toFixed(2)+'%':'—'];
    });
    table('assetsAllocation',['Класс / площадка','Оценка, ₽','Оценка Walt, USD','Доля'],rows);
    el('waltTotals').textContent=data.walt.total_usd===null?'Полная стоимость неизвестна. Известная часть: '+usd(data.walt.known_total_usd):'Итого: '+usd(data.walt.total_usd);
    const host=el('waltPositions');host.replaceChildren();
    if(!data.positions.length)host.append(txt('p','Добавь первую позицию из Walt.'));
    for(const p of data.positions){const card=txt('div','');card.className='goal-item';card.append(txt('strong',p.symbol+' · '+p.name),txt('p',data.kinds[p.kind]+' · '+p.quantity+' ед. · '+(p.value_usd===null?'Нет цены':usd(p.value_usd))),txt('small','Оценка: '+p.valued_on+' · ручной ввод'));
      const actions=txt('div','');actions.className='editor-buttons';const change=txt('button','Изменить');change.type='button';change.onclick=()=>edit(p);
      const remove=txt('button','Убрать позицию');remove.type='button';remove.onclick=async()=>{if(el('waltSave').disabled)return;if(selected?.id===p.id&&!canReplace())return;if(!confirm('Убрать '+p.symbol+' из текущего портфеля? История оценок сохранится.'))return;remove.disabled=true;try{await mutate('/walt/'+p.id,'DELETE',{revision:p.revision});if(selected?.id===p.id)reset();await refresh();feedback('waltFeedback','Позиция убрана. История сохранена.')}catch(e){feedback('waltFeedback',errorText(e),true)}finally{remove.disabled=false}};
      actions.append(change,remove);card.append(actions);host.append(card);
    }
    if(fxSnapshot()===savedFx){el('assetsFxRate').value=data.fx.usd_rub||'';el('assetsFxDate').value=data.fx.as_of||today();fxRevision=data.fx.revision;savedFx=fxSnapshot()}
    el('assetsFxInfo').textContent=data.fx.usd_rub?'Сохранённый курс: '+data.fx.usd_rub+' · '+data.fx.as_of+' · '+(data.fx.source==='cbr'?'Банк России':'ручной ввод'):'Курс ещё не сохранён.';
  }
  async function refresh(){const request=++loading;try{const result=await getJson('/api/assets/overview');if(request!==loading)return;data=result;render();
      try{const history=await getJson('/api/assets/history');if(request!==loading)return;table('assetsHistory',['Сохранено','Walt, USD','Общий портфель, ₽'],history.map(r=>[new Date(r.captured_at).toLocaleString('ru-RU'),r.walt.total_usd===null?'Неполная оценка':usd(r.walt.total_usd),r.combined.total_rub===null?'Неполная оценка':rub(Number(r.combined.total_rub))]))}catch{el('assetsHistory').replaceChildren(txt('p','История временно недоступна. Текущие данные сохранены.'))}
    }catch(e){feedback(state.page==='walt'?'waltFeedback':'assetsFeedback','Не удалось обновить данные: '+errorText(e),true)}}
  form.onsubmit=async e=>{e.preventDefault();const before=snapshot(),record=selected;const button=el('waltSave');button.disabled=true;
    try{const payload={symbol:el('waltSymbol').value,name:el('waltName').value,kind:el('waltKind').value,quantity:decimal('waltQuantity'),unit_price_usd:optional('waltPrice'),cost_usd:optional('waltCost'),valued_on:el('waltDate').value,notes:el('waltNotes').value};if(record)payload.revision=record.revision;
      const result=await mutate(record?'/walt/'+record.id:'/walt',record?'PUT':'POST',payload);selected=result;
      if(snapshot()===before)reset();else saved=before;
      await refresh();feedback('waltFeedback','Позиция сохранена. Общая оценка пересчитана; снимок добавлен в историю.');
    }catch(error){feedback('waltFeedback',errorText(error),true)}finally{button.disabled=false}};
  el('waltReset').onclick=()=>{if(!el('waltSave').disabled&&canReplace())reset()};
  el('assetsFxForm').onsubmit=async e=>{e.preventDefault();const before=fxSnapshot();const buttons=[...el('assetsFxForm').querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
    try{const result=await mutate('/fx','PUT',{rate:decimal('assetsFxRate'),as_of:el('assetsFxDate').value,revision:fxRevision});fxRevision=result.revision;savedFx=before;await refresh();feedback('assetsFeedback','Курс сохранён. Общая оценка пересчитана.')}catch(error){feedback('assetsFeedback',errorText(error),true)}finally{buttons.forEach(b=>b.disabled=false)}};
  el('assetsFxCbr').onclick=async e=>{if(fxSnapshot()!==savedFx&&!confirm('Заменить несохранённый курс значением ЦБ?'))return;const button=e.currentTarget,before=fxSnapshot();button.disabled=true;
    try{const result=await mutate('/fx/cbr','POST',{revision:fxRevision});fxRevision=result.revision;savedFx=before;await refresh();feedback('assetsFeedback','Получен официальный курс ЦБ. Цены активов Walt остаются введёнными вручную.')}catch(error){feedback('assetsFeedback',errorText(error),true)}finally{button.disabled=false}};
  el('assetsSnapshot').onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{await mutate('/snapshot','POST',{});await refresh();feedback('assetsFeedback','Снимок сохранён по ранее сохранённым позициям и курсу.')}catch(error){feedback('assetsFeedback',errorText(error),true)}finally{b.disabled=false}};
  el('assetsFxReset').onclick=()=>{if(data){el('assetsFxRate').value=data.fx.usd_rub||'';el('assetsFxDate').value=data.fx.as_of||today();fxRevision=data.fx.revision;savedFx=fxSnapshot()}};
  el('assetsFilter').onchange=render;el('assetsRefresh').onclick=el('waltRefresh').onclick=refresh;
  el('assetsOpenWalt').onclick=()=>showPage('walt');el('waltOverview').onclick=()=>showPage('assets');el('assetsBrokerLink').onclick=()=>showPage('overview');
  document.addEventListener('workspacechange',()=>{if(['assets','walt'].includes(state.page)){el('pageDesc').textContent=state.page==='assets'?'Общая оценка Т-Инвестиций и Walt с датами и полнотой данных':'Фактические остатки Walt · ручное обновление количества и цен';refresh()}});
  window.addEventListener('beforeunload',e=>{if(dirty()||fxSnapshot()!==savedFx){e.preventDefault();e.returnValue=''}});
})();

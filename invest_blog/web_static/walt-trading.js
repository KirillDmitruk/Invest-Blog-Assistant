/* Walt workspace. Calculations are visible; execution always needs explicit consent. */
(() => {
  const page = el('page-walt-agent');
  const oldHero = page.querySelector('.lab-hero');
  const oldGrid = oldHero.nextElementSibling;
  const style = document.createElement('link');
  style.rel = 'stylesheet'; style.href = '/static/walt-trading.css'; document.head.append(style);
  const root = document.createElement('div'); root.className = 'wt-desk';
  root.innerHTML = `
    <header class="wt-hero"><div><span class="wt-eyebrow">WALT · ТВОЯ ТОРГОВАЯ ЛАБОРАТОРИЯ</span>
      <h2>Рынок. Идея. Твоё решение.</h2><p>Анализируем весь каталог. Считаем риск. Открываем позицию только после твоего подтверждения.</p></div>
      <span class="wt-live-pill">Реальные средства · ручной вход</span></header>
    <div class="wt-steps" aria-label="Этапы сделки"><span><b>01</b> Анализ рынка</span><span><b>02</b> Расчёт сделки</span><span><b>03</b> Твоё подтверждение</span></div>
    <div id="wtStats" class="wt-stats"></div>
    <div class="wt-actions"><button id="wtSignal" type="button" class="wt-primary" data-wt-action>Анализировать рынок</button>
      <button id="wtRefresh" type="button" data-wt-action>Обновить позиции Walt</button>
      <button id="wtOpenSettings" type="button" class="wt-quiet">Лимиты и настройки</button></div>
    <p id="wtFeedback" class="wt-feedback" role="status" aria-live="polite" hidden></p>
    <div class="wt-progress"><p id="wtScanStatus" role="status"></p><progress id="wtScanProgress" hidden></progress></div>
    <section id="wtDraft" class="wt-calculation" aria-live="polite"></section>
    <section class="wt-section"><div class="wt-section-title"><div><span class="wt-eyebrow">ОБЗОР РЫНКА</span><h3>Активы и торговые идеи</h3></div><span id="wtMarketDate" class="wt-muted"></span></div>
      <p id="wtCoverage" class="wt-muted"></p><div id="wtStale"></div>
      <div class="wt-filters"><label>Найти актив<input id="wtSearch" class="editor-input" type="search" placeholder="Тикер: BTC, ETH, NVDA…"></label>
        <label>Показать<select id="wtIdeaFilter" class="editor-input"><option value="all">Все активы</option><option value="ideas">Идеи после проверки ИИ</option><option value="technical">Кандидаты стратегии</option><option value="missing">Нет проверенной истории</option></select></label></div>
      <div class="wt-list-tools"><p id="wtSearchCount"></p><button id="wtShowAll" type="button" class="wt-quiet" hidden aria-expanded="false" aria-controls="wtCandidates"></button></div>
      <div id="wtCandidates" class="wt-assets"></div><div id="wtDiagnostics"></div>
    </section>
    <section class="wt-section"><div class="wt-section-title"><div><span class="wt-eyebrow">ТВОЙ КОШЕЛЁК</span><h3>Открытые позиции</h3></div></div><div id="wtWallet"></div></section>
    <details class="wt-section wt-settings" id="wtLimitsDetails"><summary>Лимиты сделки</summary><p id="wtSavedLimits"></p>
      <form id="wtLimits"><div class="wt-fields"><label>Предел маржи, валюта счёта<input class="editor-input" id="wtMaxMargin" inputmode="decimal" required></label>
        <label>Риск до стопа, % капитала<input class="editor-input" id="wtRisk" inputmode="decimal" required></label>
        <label>Максимальное плечо<select class="editor-input" id="wtLeverage"><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option></select></label>
        <label>Стоп от цены, %<input class="editor-input" id="wtStop" inputmode="decimal" required></label><label>Тейк от цены, %<input class="editor-input" id="wtTake" inputmode="decimal" required></label></div>
        <button type="submit" data-wt-action>Сохранить лимиты</button></form>
      <p class="wt-muted">Маржа также ограничена 25% свободного остатка и 35% оценочного капитала. Минимум 2 единицы обеспечения не достигается за счёт превышения риска. До 4 позиций, до 2 в локальной группе, суммарный риск до стопов — до 5%. Группы приблизительные.</p></details>
    <details class="wt-section"><summary>Подключение торгового счёта</summary><p id="wtAuthState"></p>
      <button id="wtAuth" type="button" data-wt-action>Разрешить торговые права через Walt</button><a id="wtAuthLink" target="_blank" rel="noopener noreferrer" hidden>Открыть подтверждение в Walt</a>
      <p class="wt-muted">Проверь тот же пополненный агентский счёт. Авторизация сама сделку не открывает. После повторного входа нужен новый расчёт.</p>
      <button id="wtAccount" type="button" data-wt-action>Проверить / подготовить перпетуальный счёт</button>
      <p class="wt-muted">Только если Walt сообщает, что счёт не готов. Команда может запросить создание счёта; позиция не открывается.</p></details>
    <details id="wtHistoryDetails" class="wt-section"><summary>История расчётов и отправок</summary><div id="wtHistory"></div></details>
    <details id="wtAdvanced" class="wt-section"><summary>Локальный ИИ и расширенные настройки</summary><div id="wtAdvancedContent"></div></details>
    <p class="wt-footnote">Экспериментальная стратегия. Доходность не доказана. Новости имеют ограниченный охват. Анализ и расчёт не являются отправкой заявки.</p>`;
  oldHero.before(root);
  el('wtAdvancedContent').append(oldHero);
  if (oldGrid) el('wtAdvancedContent').append(oldGrid);

  let overview = null, signal = null, selected = null, dirty = false, busy = false;
  let showAll = false, pending = null, calculationError = null, draftKey = '', lastScanUpdate = 0;
  const sideName = side => side === 'long' ? 'лонг' : 'шорт';
  const directional = side => ['long', 'short'].includes(side);
  const num = (v, digits = 8) => v === null || v === undefined || v === '' ? '—' :
    Number.isFinite(Number(v)) ? new Intl.NumberFormat('ru-RU', {maximumFractionDigits: digits}).format(Number(v)) : '—';
  const time = value => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('ru-RU') : 'ещё не проверено';
  const statusName = s => ({calculated:'Расчёт готов',previewed:'Проверь и подтверди',blocked:'Исполнение заблокировано',executing:'Отправляется',submitted:'Ожидает сверки',uncertain:'Нужна проверка в Walt',protected:'Стоп и тейк подтверждены',not_open:'Сейчас не открыта',resolved_absent:'Проверка завершена · сделки нет'}[s] || s);
  const node = (tag, text, cls) => {const n = txt(tag, text); if (cls) n.className = cls; return n;};
  function message(text, tone = 'info') {const n = el('wtFeedback'); n.textContent = text; n.dataset.tone = tone; n.hidden = !text;}
  function focusSection(id) {const n = el(id); n.scrollIntoView({block:'start',behavior:'smooth'}); const heading = n.querySelector('h3,h4,summary'); if (heading) {heading.tabIndex = -1; heading.focus({preventScroll:true});}}
  function controls() {root.querySelectorAll('button[data-wt-action]').forEach(b => b.disabled = busy || b.dataset.locked === 'true');}
  function lock(button, disabled) {button.dataset.locked = String(Boolean(disabled)); button.disabled = busy || disabled;}
  function button(label, fn, cls = '', disabled = false) {
    const b = node('button', label, cls); b.type = 'button'; b.dataset.wtAction = ''; lock(b, disabled);
    b.onclick = () => run(b, fn); return b;
  }
  async function request(path, body = {}, method = 'POST') {
    // Refresh the local nonce before each user action; never retry a mutation.
    const token = await getJson('/api/editor-token'); state.editorToken = token.token;
    const response = await fetch('/api/walt-trading' + path, {method,headers:{'Content-Type':'application/json','X-CSRF-Token':token.token},body:JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(e => e.msg).join('. ') : data.detail;
      throw Error(typeof detail === 'string' ? detail : 'Хаб не подтвердил действие (HTTP ' + response.status + '). Повтори после проверки состояния.');
    }
    return data;
  }
  async function run(control, fn) {
    if (busy) return;
    busy = true; controls(); const label = control.textContent; control.textContent = 'Подожди…';
    try {await fn();} catch (e) {message(e.message, 'error');}
    finally {busy = false; if (control.isConnected) control.textContent = label; controls(); tick();}
  }
  function openLimits() {el('wtLimitsDetails').open = true; focusSection('wtLimitsDetails');}
  function resetList() {showAll = false; renderCandidates();}

  function renderStats() {
    const host = el('wtStats'); host.replaceChildren();
    const d = overview?.diagnostics, wallet = overview?.wallet_snapshot;
    for (const [label,value,note] of [['Проверено активов',signal?.total ?? '—','Анализ всего каталога'],['Кандидаты стратегии',d?.technical ?? '—','Прошли технические условия'],['Идеи после ИИ',d?.approved ?? '—','Для ручного рассмотрения'],['Открытые позиции',wallet?.open_count ?? '—',wallet ? 'По последней проверке Walt' : 'Нажми «Обновить позиции»']]) {
      const card = node('div','','wt-stat'); card.append(node('span',label),node('strong',String(value)),node('small',note)); host.append(card);
    }
  }
  function renderCandidates() {
    const host = el('wtCandidates'); host.replaceChildren();
    el('wtMarketDate').textContent = signal ? 'Анализ: ' + time(signal.created) : 'Ещё не запускался';
    el('wtCoverage').textContent = signal?.coverage || 'Начни с анализа рынка. Он проверит весь каталог, а здесь покажет первые 10 активов.';
    const stale = el('wtStale'); stale.replaceChildren();
    if (signal && Date.now()/1000 > signal.expires) {
      const hint = node('div','','wt-notice'); hint.append(node('span','Анализ устарел. Перед расчётом обнови рыночные данные.'),button('Обновить анализ', analyse, 'wt-quiet')); stale.append(hint);
    }
    if (!signal) {host.append(node('p','После анализа здесь появятся активы, идеи и объяснения.','wt-empty')); el('wtSearchCount').textContent = ''; el('wtShowAll').hidden = true; return;}
    const query = el('wtSearch').value.trim().toUpperCase(), mode = el('wtIdeaFilter').value;
    const rank = r => Number(directional(r.ai.side))*2 + Number(directional(r.assessment?.rule_side));
    const filtered = signal.candidates.filter(r => r.symbol.toUpperCase().includes(query) &&
      (mode === 'all' || mode === 'ideas' && directional(r.ai.side) || mode === 'technical' && directional(r.assessment?.rule_side) || mode === 'missing' && !r.assessment?.data_verified))
      .sort((a,b) => rank(b)-rank(a));
    const visible = showAll ? filtered : filtered.slice(0,10);
    el('wtSearchCount').textContent = 'Показано ' + visible.length + ' из ' + filtered.length + (filtered.length !== signal.candidates.length ? ' · всего в каталоге ' + signal.candidates.length : '');
    const more = el('wtShowAll'); more.hidden = filtered.length <= 10;
    more.textContent = showAll ? 'Свернуть до 10' : 'Показать все ' + filtered.length + ' активов'; more.setAttribute('aria-expanded',String(showAll));
    if (!visible.length) host.append(node('p','Ничего не найдено. Измени тикер или фильтр.','wt-empty'));
    for (const item of visible) {
      const a = item.assessment, ai = item.ai, technical = directional(a?.rule_side);
      const tone = directional(ai.side) ? ai.side : technical ? 'watch' : 'wait';
      const card = node('article','','card wt-asset'); card.dataset.tone = tone;
      const head = node('div','','wt-asset-head'), brand = node('span',item.symbol.slice(0,2),'wt-coin');
      const title = node('div','','wt-asset-title'); title.append(node('h4',item.symbol),node('small','Перпетуальный контракт'));
      const price = node('div','','wt-price'); price.append(node('strong',item.price_available === false ? 'Нет котировки' : num(item.price)),node('small',item.currency));
      head.append(brand,title,price); card.append(head);
      card.append(node('span',directional(ai.side) ? 'Идея: ' + sideName(ai.side) + ' · эксперимент' : technical ? 'Кандидат стратегии · ИИ выбрал ожидание' : 'Подтверждённого сигнала нет','wt-badge'));
      card.append(node('p',item.price_available === false ? item.price_error : ai.reason,'wt-asset-reason'));
      if (a?.data_verified) {
        const metrics = node('div','','wt-mini-metrics');
        for (const [name,value,kind] of [['За сутки',a.metrics.change_24h_pct,'pct'],['За неделю',a.metrics.change_7d_pct,'pct'],['RSI',a.metrics.rsi14,'number']]) {
          const part = node('div',''); part.append(node('small',name),node('strong',num(value,2)+(kind === 'pct' ? '%' : ''))); if (kind === 'pct') part.dataset.positive = String(value >= 0); metrics.append(part);
        }
        card.append(metrics);
      }
      const details = node('div','','wt-asset-details'); renderEvidence(details,a); renderNews(details,item.news,item.model_review?.news_ids || []); card.append(details);
      const local = node('p','','wt-card-feedback'); local.hidden = true; local.setAttribute('role','status'); card.append(local);
      const actions = node('div','','wt-asset-actions');
      for (const side of ['long','short']) actions.append(button('Рассчитать ' + sideName(side), () => calculate(item,side,local), side === 'long' ? 'wt-long' : 'wt-short', item.price_available === false));
      card.append(actions); host.append(card);
    }
  }
  async function calculate(item,side,local) {
    pending = {symbol:item.symbol,side}; selected = null; calculationError = null; renderDraft(true); focusSection('wtDraft');
    if (local) {local.hidden = false; local.textContent = 'Проверяем баланс, цену и риск…';}
    try {
      if (dirty) throw Error('Есть несохранённые лимиты. Нажми «Сохранить лимиты» перед расчётом.');
      if (!signal || Date.now()/1000 > signal.expires) throw Error('Анализ устарел. Нажми «Анализировать рынок», затем повтори расчёт.');
      selected = await request('/quote',{signal_id:signal.id,contract_id:item.contract_id,side});
      pending = null; renderDraft(true);
      if (local) local.textContent = 'Расчёт готов — он показан над списком активов.';
      message('Расчёт ' + item.symbol + ' готов. Проверь сумму и уровни. Заявка ещё не отправлена.','success');
      try {await refresh();} catch (e) {message('Расчёт готов, но сводку не удалось обновить. '+e.message);}
      focusSection('wtDraft');
    } catch (e) {
      pending = null; calculationError = {symbol:item.symbol,side,text:e.message}; renderDraft(true);
      if (local) local.textContent = e.message; message(e.message,'error');
      focusSection('wtDraft');
    }
  }
  function renderDraft(force = false) {
    const host = el('wtDraft');
    const key = JSON.stringify([pending,calculationError,selected?.id,selected?.status,selected?.preview_hash,selected?.preview_expires,selected?.alert,selected?.block_reason,selected?.local_uncertain,selected?.orders_checked?.at,selected?.actual_position?.checked_at]);
    if (!force && key === draftKey) return; draftKey = key; host.replaceChildren();
    host.classList.toggle('is-active',Boolean(pending || calculationError || selected));
    if (pending) {host.append(node('span','РАСЧЁТ СДЕЛКИ','wt-eyebrow'),node('h3',pending.symbol + ' · ' + sideName(pending.side)),node('p','Проверяем свободный остаток, актуальную цену и ограничения риска…','wt-loading')); return;}
    if (calculationError) {
      host.dataset.tone = 'error'; host.append(node('span','РАСЧЁТ НЕ ВЫПОЛНЕН','wt-eyebrow'),node('h3',calculationError.symbol + ' · ' + sideName(calculationError.side)),node('p',calculationError.text,'wt-error-text'));
      const actions = node('div','','wt-actions'); actions.append(button('Обновить анализ',analyse,'wt-primary')); const limits = node('button','Изменить лимиты'); limits.type = 'button'; limits.onclick = openLimits; actions.append(limits); host.append(actions,node('small','Заявка не отправлена. Лимиты автоматически не увеличиваются.')); return;
    }
    if (!selected) {host.removeAttribute('data-tone'); host.append(node('span','СЛЕДУЮЩИЙ ШАГ','wt-eyebrow'),node('h3','Выбери актив и направление'),node('p','Нажми «Рассчитать лонг» или «Рассчитать шорт». Сумма, риск и уровни появятся здесь.','wt-muted')); return;}
    const p = selected.proposal; const actual = selected.positions_seen?.length ? selected.actual_position : null; const localEstimate = p.liquidation_source === 'local_conservative_estimate'; const liquidationLabel = localEstimate ? 'Граница защиты · оценка хаба' : 'Ликвидация Walt'; host.dataset.tone = selected.local_uncertain || selected.alert || selected.block_reason ? 'error' : p.side;
    const title = node('div','','wt-section-title'); title.append(node('h3',p.symbol + ' · ' + sideName(p.side)),node('span',statusName(selected.local_uncertain ? 'uncertain' : selected.status),'wt-badge')); host.append(title);
    const metrics = node('div','','wt-draft-metrics');
    const estimatedMetrics = [['Маржа',num(p.margin)+' '+p.currency,''],['Плечо',p.leverage+'×',''],['Риск с расходами',num(p.risk_estimate)+' '+p.currency,'risk'],['Стоп',num(p.stop),'risk'],['Тейк',num(p.take),'profit'],['Оценка прибыли у тейка',num(p.profit_estimate)+' '+p.currency,'profit'],['Цена-ссылка Walt',num(p.reference_price),''],[liquidationLabel,p.liquidation ? num(p.liquidation) : 'Не подтверждена','risk'],['Фандинг за час',(Number(p.funding_hour_estimate)>=0?'−':'＋')+num(Math.abs(Number(p.funding_hour_estimate)))+' '+p.currency,'']];
    const actualMetrics = actual ? [['Маржа Walt',num(actual.margin_quote)+' '+p.currency,''],['Фактический вход',num(actual.avg_entry_price),''],['Объём позиции',num(actual.value_base),''],['Стоп Walt',num(actual.stop),'risk'],['Тейк Walt',num(actual.take),'profit'],['Ликвидация Walt',num(actual.liquidation_price),'risk'],['Фандинг списан Walt',num(actual.funding_paid_quote)+' '+p.currency,'']] : null;
    for (const [label,value,tone] of actualMetrics || estimatedMetrics) {
      const cell = node('div',''); cell.dataset.tone = tone; cell.append(node('small',label),node('strong',value)); metrics.append(cell);
    }
    host.append(metrics);
    if(actual)host.append(node('p','Фактическая позиция Walt · проверено '+time(actual.checked_at)+'. '+(selected.status==='protected'?'Стоп и тейк сверены.':'Защитные уровни требуют проверки.'),'wt-notice'));
    if(localEstimate && !actual)host.append(node('p','Walt не вернул цену ликвидации в предпросмотре. Показана консервативная расчётная граница хаба, которая может отличаться от фактической. После открытия проверяем фактическую позицию.','wt-notice'));
    const additional = node('details',''); additional.append(node('summary',actual?'Расчёт перед отправкой · справочно':'Цена, объём и стоимость удержания'));
    for (const [label,value] of [['Цена-ссылка Walt',num(p.reference_price)],['Оценка номинала',num(p.notional_estimate)+' '+(p.quote_currency || p.currency)],[liquidationLabel,p.liquidation ? num(p.liquidation) : 'До предпросмотра не подтверждена'],['Фандинг за час по текущей ставке',(Number(p.funding_hour_estimate) >= 0 ? 'Оплата ≈ ' : 'Получение ≈ ')+num(Math.abs(Number(p.funding_hour_estimate)))+' '+p.currency],['Свободный остаток',num(p.free_balance)+' '+p.currency]]) additional.append(node('p',label+': '+value)); host.append(additional);
    const basis = node('details',''); basis.append(node('summary','Основания и ограничения'),node('p',selected.ai.reason));
    renderEvidence(basis,selected.assessment); renderNews(basis,selected.news,selected.model_review?.news_ids || []);
    for (const warning of selected.warnings || []) basis.append(node('p',warning)); host.append(basis);
    for (const alert of [selected.alert,selected.block_reason,selected.local_uncertain && 'Ответ отправки не подтверждён. Сначала обнови позиции Walt. Автоматического повтора нет.']) if (alert) host.append(node('p',alert,'wt-error-text'));
    const actions = node('div','','wt-actions');
    if (['calculated','previewed','blocked'].includes(selected.status)) {
      const expired = Date.now()/1000 > selected.expires;
      if (expired) host.append(node('p','Расчёт устарел. Нужен новый расчёт по свежим данным.','wt-error-text'));
      else actions.append(button('Проверить сделку в Walt',async () => {
        selected = await request('/drafts/'+selected.id+'/prepare'); renderDraft(true);
        message(selected.block_reason || 'Предпросмотр готов. Проверь параметры и подтверди сделку вручную.',selected.block_reason ? 'error' : 'success');
      },'wt-primary',!overview?.trade_authorized));
      if (!overview?.trade_authorized) host.append(node('p','Для предпросмотра нужны торговые права. Открой «Подключение торгового счёта» ниже.','wt-muted'));
    }
    if (selected.status === 'previewed' && !selected.local_uncertain) {
      host.append(node('p','','wt-expiry')); host.querySelector('.wt-expiry').id = 'wtExpiry';
      const consent = node('label','','wt-consent'), check = document.createElement('input'); check.type = 'checkbox';
      consent.append(check,node('span','Подтверждаю реальную сделку: '+p.symbol+' '+sideName(p.side)+', маржа '+num(p.margin)+' '+p.currency+', '+p.leverage+'×, стоп '+num(p.stop)+', тейк '+num(p.take)+'.'+(localEstimate?' Граница защиты '+num(p.liquidation)+' — оценка хаба, не подтверждённая цена Walt.':''))); host.append(consent);
      const id = selected.id, hash = selected.preview_hash;
      const execute = button('Открыть реальную позицию в Walt',async () => {
        if (!check.checked || Date.now()/1000 > selected.preview_expires) throw Error('Подтверждение истекло. Запроси новый предпросмотр.');
        if (!confirm('Открыть '+p.symbol+' '+sideName(p.side)+' на реальном счёте? Маржа '+p.margin+' '+p.currency+', '+p.leverage+'×. Стоп '+p.stop+', тейк '+p.take+'. '+(localEstimate?'Расчётная граница защиты '+p.liquidation+' — оценка хаба, а не цена Walt.':'Оценка ликвидации Walt '+p.liquidation+'.'))) return;
        try {selected = await request('/drafts/'+id+'/execute',{preview_hash:hash,confirm:'OPEN_REAL_POSITION'}); await refresh(); watchSettlement(id); renderDraft(true); message(selected.status === 'protected' ? 'Позиция обнаружена, стоп и тейк подтверждены.' : selected.alert || 'Проверь состояние отправки в Walt.',selected.status === 'protected' ? 'success' : 'info');}
        catch (e) {selected.local_uncertain = true; renderDraft(true); throw Error('Результат отправки не подтверждён. Обнови позиции Walt перед дальнейшими действиями. '+e.message);}
      },'wt-primary',true);
      execute.id = 'wtExecute'; check.onchange = () => lock(execute,!check.checked || Date.now()/1000 > selected.preview_expires); actions.append(execute);
    }
    if (['submitted','executing','uncertain','protected','not_open'].includes(selected.status)) actions.append(button('Проверить позиции, заявки и защитные уровни',async () => {selected = await request('/drafts/'+selected.id+'/reconcile'); await refresh(); renderDraft(true);}));
    if (selected.status === 'uncertain' && selected.orders_checked?.complete && !selected.orders_checked.orders.length && !selected.positions_seen?.length && Date.now()/1000-selected.created>=120) {
      actions.append(button('Я проверил Walt — позиции и заявки нет',async () => {
        if(!confirm('Ты самостоятельно проверил Walt: позиции и ожидающей заявки по этому активу нет? Причина прошлой отправки неизвестна. Старое подтверждение будет аннулировано. Новая сделка потребует отдельного расчёта и подтверждения.'))return;
        selected=await request('/drafts/'+selected.id+'/resolve-absent',{confirm:'CONFIRM_NO_POSITION_OR_ORDER'});await refresh();renderDraft(true);
      }));
    }
    if(selected.execution_issue && selected.status!=='protected'){const issue=selected.execution_issue;host.append(node('p','Диагностика отправки: '+issue.phase+' · '+issue.kind+(issue.code?' · '+issue.code:'')+(issue.http_status?' · HTTP '+issue.http_status:'')+' · '+time(issue.at),'wt-muted'));}
    if(selected.orders_checked){const check=selected.orders_checked;const details=node('details','');details.open=true;details.append(node('summary','Заявки Walt · '+time(check.at)));details.append(node('p',check.orders.length?'Получено заявок: '+check.orders.length:'Заявок не найдено. Отсутствие записи само по себе не подтверждает причину сбоя.'));for(const order of check.orders)details.append(node('p','ID '+(order.order_id??order.id??'не указан')+' · '+(order.status??'статус не указан')));if(!check.complete)details.append(node('p','Список неполный — остаются страницы.'));host.append(details);}
    host.append(actions,node('small',actual?'Показан последний прочитанный снимок позиции. Рыночные стопы не гарантируют цену исполнения.':'Рыночная цена исполнения и прибыль не гарантированы. Открытие — только после отдельного подтверждения.','wt-muted'));
    if(selected.liquidation_evidence){const proof=node('details','');proof.append(node('summary','Как проверен запас защиты'),node('p',selected.liquidation_evidence.note),node('p','Оценка исполнения стопа: '+num(selected.liquidation_evidence.stop_fill_estimate)+' · дополнительный ценовой запас: '+num(selected.liquidation_evidence.buffer)));host.append(proof);}
    if (selected.provider) {const details = node('details',''); details.append(node('summary','Ответ и предупреждения Walt'),node('pre',JSON.stringify(selected.provider,null,2))); host.append(details);}
    tick();
  }
  function tick() {
    const expiry = el('wtExpiry'); if (!expiry || !selected) return;
    const left = Math.max(0,Math.floor(selected.preview_expires-Date.now()/1000));
    expiry.textContent = left ? 'Подтверждение Walt действует ещё '+left+' сек.' : 'Подтверждение истекло. Запроси новый предпросмотр.';
    if (!left && el('wtExecute')) lock(el('wtExecute'),true);
  }
  function renderEvidence(host,a) {
    if (!a?.data_verified) return;
    const m = a.metrics, bt = a.backtest, details = node('details','','wt-evidence'); details.append(node('summary','Данные и правила стратегии'));
    details.append(node('p','Правила: '+(directional(a.rule_side) ? 'технический кандидат в '+sideName(a.rule_side) : 'ожидание')),node('p','Источник: '+a.history.source+' · '+a.history.resolution+' · '+time(a.history.from)+' — '+time(a.history.to)),node('p','EMA20 '+num(m.ema20)+' · EMA50 '+num(m.ema50)+' · RSI '+num(m.rsi14,1)+' · ATR '+num(m.atr_pct,2)+'%'));
    for (const c of a.checks) details.append(node('p',(c.ok ? '✓ ' : '— ')+c.text));
    details.append(node('p',a.oi ? 'OI в USD: '+num(a.oi.change_pct,2)+'% за '+num(a.oi.hours,1)+' ч по сохранённым наблюдениям.' : 'Изменение OI: нет сравнимого наблюдения за сутки.'));
    details.append(node('p','Смоделированных сделок стратегии (не твоих): '+bt.trades+' · средняя R: '+num(bt.average_r,2)+' · просадка: '+num(bt.max_drawdown_r,2)+' R.'),node('small','R — риск сделки, не доходность счёта. Исторический фандинг в ретроспективу не включён.'),node('p',a.risk_notice)); host.append(details);
  }
  function renderNews(host,news,references) {
    const details = node('details','','wt-news'); details.append(node('summary','Новости · '+(news?.items?.length || 0)));
    if (!news) details.append(node('p','Для сохранённого анализа новости не собирались. Обнови анализ.'));
    else {
      details.append(node('p',({matched:'Сообщения по активу',macro_only:'Только общий макрофон',no_matches:'Совпадений в доступных RSS нет',unavailable:'Новостные источники недоступны'}[news.status] || news.status)),node('small','Проверено: '+time(news.checked_at)));
      for (const item of news.items || []) {
        const row = node('div','','wt-news-item'); row.append(node('small',(item.scope === 'macro' ? 'Общий рынок · ' : '')+item.source+' · '+time(item.published_at)+(item.cache_stale ? ' · сохранённая копия' : '')));
        const link = node('a',item.title); try {const u = new URL(item.url); if (u.protocol === 'https:' && !u.username && !u.password) {link.href = item.url; link.target = '_blank'; link.rel = 'noopener noreferrer';}} catch (_) {}
        row.append(link); if (item.summary) row.append(node('p',item.summary)); if (references.includes(item.id)) row.append(node('strong','ИИ сослался на это сообщение при оценке идеи.')); details.append(row);
      }
      details.append(node('small',news.note));
    }
    host.append(details);
  }
  function renderDiagnostics() {
    const host = el('wtDiagnostics'); host.replaceChildren(); if (!signal || !overview) return;
    const details = node('details','','wt-diagnostics'); details.append(node('summary','Охват анализа и причины ожидания'));
    details.append(node('p',signal.limitations));
    const names = {trend:'Тренд, EMA или RSI',liquidity:'Недостаточный объём',retrospective:'Неположительная ретроспектива',daily_move:'Слишком сильное суточное движение',volatility:'Стоп узкий для волатильности',price_match:'Расхождение с историей',history_fresh:'Устаревшая история',funding:'Дорогой фандинг',venue:'Режим торговли не подтверждён'};
    for (const [key,count] of Object.entries(overview.diagnostics?.blocked_by || {}).sort((a,b) => b[1]-a[1])) details.append(node('p',(names[key] || key)+': '+count));
    details.append(node('small','Один инструмент может не пройти несколько условий.'));
    if (signal.news_summary) for (const source of signal.news_summary.sources || []) details.append(node('p',source.source+' · '+(source.status === 'ok' ? 'доступен' : 'ошибка / кэш')+' · сообщений: '+source.recent_items)); host.append(details);
  }
  function renderWallet() {
    const host = el('wtWallet'), wallet = overview?.wallet_snapshot; host.replaceChildren();
    if (!wallet) {host.append(node('p','Нажми «Обновить позиции Walt», чтобы прочитать состояние кошелька.','wt-empty')); return;}
    host.append(node('p','Последняя проверка: '+time(wallet.checked_at),'wt-muted'));
    if (!wallet.open_count) {host.append(node('p','На момент проверки открытых позиций в Walt нет. Анализ и расчёт сами по себе позицию не открывают.','wt-empty')); return;}
    for (const p of wallet.positions) {const card = node('div','','wt-position'); card.append(node('strong',(p.symbol || 'Контракт '+p.contract_id)+' · '+(directional(p.side) ? sideName(p.side) : 'сторона неизвестна')),node('p','Маржа: '+(p.margin_quote ?? 'не подтверждена')+' · вход: '+(p.avg_entry_price ?? 'не подтверждён')),node('p','Стоп: '+(p.stop ?? 'НЕ ПОДТВЕРЖДЁН')+' · тейк: '+(p.take ?? 'не подтверждён')+' · ликвидация: '+(p.liquidation_price ?? 'не подтверждена'))); host.append(card);}
  }
  async function refresh() {
    const data = await getJson('/api/walt-trading/overview'); overview = data;
    if (!signal || (data.latest_signal?.created || 0) > (signal.created || 0)) signal = data.latest_signal;
    if (selected) {const updated = data.drafts.find(d => d.id === selected.id); if (updated) {const uncertain = selected.local_uncertain && updated.status === 'previewed' && selected.preview_hash === updated.preview_hash; selected = {...updated,local_uncertain:uncertain};}}
    if (!dirty) {
      const l = data.limits; el('wtMaxMargin').value = l.max_margin; el('wtRisk').value = l.risk_pct; el('wtLeverage').value = l.leverage; el('wtStop').value = l.stop_pct; el('wtTake').value = l.take_pct;
      el('wtSavedLimits').textContent = 'Сохранено: плечо до '+l.leverage+'× · риск '+l.risk_pct+'% · стоп '+l.stop_pct+'% · тейк '+l.take_pct+'%.';
    }
    el('wtAuthState').textContent = data.trade_authorized ? 'Торговые права сохранены. Каждая позиция требует твоего подтверждения.' : 'Доступно чтение. Для предпросмотра сделки отдельно подтверди торговые права.';
    const history = el('wtHistory'); history.replaceChildren();
    for (const draft of data.drafts) history.append(button(draft.proposal.symbol+' · '+sideName(draft.proposal.side)+' · '+statusName(draft.status),async () => {selected = draft; pending = calculationError = null; renderDraft(true); focusSection('wtDraft');},'wt-history-item'));
    if (!data.drafts.length) history.append(node('p','Расчётов и отправок пока нет.','wt-muted'));
    renderStats(); renderCandidates(); renderDiagnostics(); renderWallet(); renderDraft(); controls();
  }
  let settlementTimer=null;
  function watchSettlement(id,remaining=6) {
    clearTimeout(settlementTimer);
    if(!remaining || selected?.id!==id || selected.positions_seen?.length || !['submitted','uncertain'].includes(selected.status))return;
    settlementTimer=setTimeout(async()=>{
      if(document.hidden || state.page!=='walt-agent' || busy || selected?.id!==id)return;
      try {
        await request('/wallet-refresh');await refresh();
        if(selected?.status==='protected')message('Позиция открыта. Фактические стоп, тейк и ликвидация прочитаны из Walt.','success');
        else watchSettlement(id,remaining-1);
      } catch(e){message('Автопроверка остановлена. Нажми «Обновить позиции Walt». '+e.message,'error');}
    },10000);
  }
  async function analyse() {
    message('Проверяем весь каталог, историю, новости и идеи. Прогресс показан ниже.');
    signal = await request('/signal'); showAll = false; selected = pending = calculationError = null; await refresh();
    message('Анализ готов. Кандидатов стратегии: '+overview.diagnostics.technical+' · идей после ИИ: '+overview.diagnostics.approved+'. Заявки не отправлялись.','success');
  }
  async function scanStatus() {
    if (state.page !== 'walt-agent') return;
    try {
      const p = await getJson('/api/walt-trading/scan-status'), active = p.status === 'running' && Date.now()/1000-p.updated < 120;
      el('wtScanProgress').hidden = !active; el('wtScanProgress').max = p.total || 1; el('wtScanProgress').value = p.done;
      el('wtScanStatus').textContent = active ? ({history:'История Lighter',news:'Новости',quotes:'Котировки и правила',model:'Проверка ИИ'}[p.phase] || 'Проверка рынка')+' · '+(p.symbol || '')+' · '+p.done+' из '+p.total : p.status === 'failed' ? 'Проверка не завершилась. Можно запустить её снова.' : '';
      lock(el('wtSignal'),active);
      if (!busy && p.status === 'complete' && p.updated > lastScanUpdate) {lastScanUpdate = p.updated; if (!signal || signal.created < (p.started || 0)) await refresh();}
    } catch (e) {el('wtScanStatus').textContent = 'Не удалось обновить прогресс. '+e.message;}
  }
  el('wtSignal').onclick = () => run(el('wtSignal'),analyse);
  el('wtRefresh').onclick = () => run(el('wtRefresh'),async () => {message('Читаем открытые позиции в Walt…'); const result = await request('/wallet-refresh'); await refresh(); message('Открытых позиций: '+result.open_count+'. Проверено в '+time(result.checked_at)+'. Новые заявки не отправлялись.','success');});
  el('wtOpenSettings').onclick = openLimits;
  el('wtSearch').oninput = resetList; el('wtIdeaFilter').onchange = resetList;
  el('wtShowAll').onclick = () => {showAll = !showAll; renderCandidates();};
  el('wtLimits').oninput = el('wtLimits').onchange = () => {dirty = true; el('wtSavedLimits').textContent = 'Есть несохранённые изменения. Нажми «Сохранить лимиты».';};
  el('wtLimits').onsubmit = e => {
    e.preventDefault(); const control = el('wtLimits').querySelector('button');
    run(control,async () => {
      if (!overview) throw Error('Дождись загрузки настроек.');
      const decimal = id => el(id).value.trim().replace(',','.');
      const l = {...overview.limits,max_margin:decimal('wtMaxMargin'),risk_pct:decimal('wtRisk'),leverage:Number(el('wtLeverage').value),stop_pct:decimal('wtStop'),take_pct:decimal('wtTake')};
      await request('/limits',l,'PUT'); dirty = false; selected = pending = calculationError = null; await refresh();
      message('Лимиты сохранены: плечо до '+l.leverage+'×, риск '+l.risk_pct+'%. Теперь рассчитай выбранный актив заново. После изменения стопа/тейка обнови анализ.','success');
    });
  };
  el('wtAuth').onclick = () => run(el('wtAuth'),async () => {const result = await request('/auth'); el('wtAuthLink').href = result.url; el('wtAuthLink').hidden = false; message('Открой ссылку и самостоятельно подтверди торговые права в Walt. После входа обнови страницу и пересчитай сделку.');});
  el('wtAccount').onclick = () => run(el('wtAccount'),async () => {if (!confirm('Проверить готовность счёта? Если его ещё нет, Walt может запросить создание. Позиция не открывается.')) return; const result = await request('/account-setup',{confirm:'PREPARE_PERPS_ACCOUNT'}); message(result.ok ? 'Walt подтвердил готовность счёта.' : JSON.stringify(result));});
  document.addEventListener('workspacechange',() => {if (state.page === 'walt-agent') {el('pageDesc').textContent = 'Walt · Анализ рынка, риск и ручное подтверждение'; refresh().catch(e => message(e.message,'error')); scanStatus();}});
  window.addEventListener('beforeunload',e => {if (dirty) {e.preventDefault(); e.returnValue = '';}});
  setInterval(scanStatus,2500); setInterval(tick,1000);
  renderStats(); renderCandidates(); renderDraft(); renderWallet();
})();

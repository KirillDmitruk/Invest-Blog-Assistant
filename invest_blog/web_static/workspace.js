/* Shared navigation and writing tools; existing domain workflows keep their APIs. */
(() => {
  const nav = document.querySelector('.nav');
  const oldButtons = [...nav.querySelectorAll('button')];
  const tabs = document.createElement('nav');
  tabs.className = 'workspace-tabs'; tabs.setAttribute('aria-label', 'Подразделы');
  document.querySelector('.top').after(tabs);
  const groups = [
    ['home', '⌂ Главная', ['home']],
    ['analytics', '◫ Аналитика', ['overview', 'performance']],
    ['portfolio', '▤ Портфель', ['assets', 'walt', 'bonds', 'risk', 'iis']],
    ['cashflow', '▦ Денежные потоки', ['income', 'calendar']],
    ['strategy', '◎ Стратегия', ['goals', 'allocation']],
    ['placements', '🛰 Новые размещения', ['placements']],
    ['news', '⚙ Tech · Новости', ['news', 'publishing']],
    ['editorial', '✎ Редакция', ['desk', 'content', 'tech', 'creator']],
  ];
  const labels = {assets:'Все активы',walt:'Walt · Позиции',desk:'Общая очередь',content:'Инвестиции', tech:'Tech · Черновики', creator:'Creator Studio', news:'Лента новостей', publishing:'Публикация и история'};
  const buttons = new Map(oldButtons.map(b => [b.dataset.page || 'iis', b]));
  // Split the long Tech page without copying forms or event handlers.
  for (const [page, anchor] of [['news','techNewsList'],['publishing','techPublishStatus']]) {
    const section = txt('section',''); section.id = 'page-'+page; section.className='section';
    section.append(el(anchor).closest('.card')); el('page-tech').after(section);
    const button = txt('button', labels[page]); button.type='button';button.dataset.page=page;
    button.addEventListener('click',()=>showPage(page)); buttons.set(page,button);
  }
  el('page-tech').querySelector('.hint').textContent='Черновики технологического блога. Лента новостей и управление отправкой доступны в разделе Tech · Новости.';
  const desk=txt('section','');desk.id='page-desk';desk.className='section';
  desk.innerHTML='<div class="card"><div class="editor-toolbar"><h2>Редакционная очередь</h2><button type="button" class="editor-btn" id="deskRefresh">Обновить</button></div><p class="hint">Последние 100 материалов каждого блога и текущий Creator-план в одном списке. Открой материал для редактирования и проверки перед публикацией.</p><div class="editor-fields"><label>Найти материал<input class="editor-input" id="deskSearch" type="search" placeholder="Название или текст"></label><label>Направление<select class="editor-input" id="deskProject"><option value="">Все направления</option><option value="content">Инвестиции</option><option value="tech">Tech</option><option value="creator">Creator Studio</option></select></label></div><div id="deskFeedback" role="status"></div><div id="deskList"></div></div>';
  el('page-content').before(desk);
  buttons.set('desk',Object.assign(txt('button',labels.desk),{type:'button'}));buttons.get('desk').onclick=()=>showPage('desk');
  let deskRows=[];
  function renderDesk(){const query=el('deskSearch').value.toLocaleLowerCase('ru'),project=el('deskProject').value,host=el('deskList');host.replaceChildren();
    const rows=deskRows.filter(r=>(!project||r.project===project)&&(r.title+' '+r.body).toLocaleLowerCase('ru').includes(query));
    for(const row of rows){const card=txt('div','');card.className='creator-entry';const info=txt('div','');info.append(txt('strong',row.title),txt('p',labels[row.project]+' · '+row.status));const open=txt('button','Открыть');open.type='button';open.onclick=async()=>{if(row.project==='content'){showPage('content');await openDraft(row.id)}else if(row.project==='creator'){showPage('creator');openCreator(row.raw)}else if(discard(el('techForm'))){showPage('tech');activeTech=row.raw;el('techTitle').value=row.raw.title;el('techBody').value=row.raw.body;el('techSource').value=row.raw.source_url||'';remember(el('techForm'));recordChanged()}};card.append(info,open);host.append(card)}
    if(!rows.length)host.append(txt('p','Материалов по выбранному фильтру нет.'));
  }
  async function refreshDesk(){const paths=['/api/drafts?include_body=true','/api/tech/drafts','/api/creator/studio'];const results=await Promise.allSettled(paths.map(getJson));const projects=['content','tech','creator'];const failures=[];
    results.forEach((r,i)=>{if(r.status==='rejected'){failures.push(labels[projects[i]]);return}const project=projects[i];deskRows=deskRows.filter(x=>x.project!==project);const rows=i===2?r.value.posts:r.value;for(const d of rows){if(d.state==='deleted')continue;deskRows.push({id:d.id,project,title:d.title||((editorialLabels[d.kind]||d.kind)+' · '+d.period),body:d.body||d.notes||'',status:editorialStatuses[d.state]||creatorLabels[d.status]||d.state||d.status,raw:d})}});
    el('deskFeedback').replaceChildren(txt('p',failures.length?'Не удалось обновить: '+failures.join(', ')+'. Предыдущие данные сохранены.':'Материалов: '+deskRows.length));renderDesk();
  }
  el('deskSearch').oninput=renderDesk;el('deskProject').onchange=renderDesk;el('deskRefresh').onclick=refreshDesk;
  const primary = new Map(); nav.replaceChildren();
  for (const [key,label,pages] of groups) {
    const b=txt('button',label);b.type='button';b.addEventListener('click',()=>showPage(pages[0]));
    primary.set(key,b);nav.append(b);
  }
  const originalShowPage = showPage;
  showPage = function(page) {
    if (page==='iis') {window.location.href='/iis';return}
    if (!el('page-'+page)) page='home';
    originalShowPage(page);
    const group=groups.find(g=>g[2].includes(page)); tabs.replaceChildren();
    for(const [key,b] of primary) {b.classList.toggle('active',key===group[0]);if(key===group[0])b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')}
    if(group[2].length>1) for(const target of group[2]) {
      const b=buttons.get(target);if(!b)continue;
      if(labels[target])b.textContent=labels[target];
      b.classList.toggle('active',target===page);
      if(target===page)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');
      tabs.append(b);
    }
    if(['desk','content','tech','creator'].includes(page)) {
      el('pageTitle').textContent='Редакция · '+labels[page];
      el('pageDesc').textContent='Создание → редактура → перевод и SEO → проверка → публикация';
    } else if(labels[page]) el('pageTitle').textContent=labels[page];
    if(location.hash!=='#'+page) history.replaceState(null,'','#'+page);
    document.dispatchEvent(new Event('workspacechange'));
    if(page==='desk')refreshDesk();
  };
  window.addEventListener('hashchange',()=>showPage(location.hash.slice(1)));

  // Snapshot every form, including unsaved new material. Navigation keeps forms mounted.
  const tracked = ['editorForm','techForm','creatorForm'].map(id=>el(id));
  const snapshots = new WeakMap();
  function snapshot(form) {return JSON.stringify([...form.querySelectorAll('input,select,textarea')].map(e=>[e.id,e.value]))}
  function remember(form) {snapshots.set(form,snapshot(form))}
  function dirty(form) {return snapshots.get(form)!==snapshot(form)}
  tracked.forEach(remember);
  window.addEventListener('beforeunload',e=>{if(tracked.some(dirty)){e.preventDefault();e.returnValue=''}});
  // Switching documents or resetting must not discard unsaved work silently.
  const recordChanged=()=>document.dispatchEvent(new Event('editorrecordchange'));
  const rawNew = newDraft, rawOpen = openDraft, rawReset = resetTech, rawCreator=openCreator, rawTechOpen=openTechDraft;
  const discard = form => !dirty(form)||confirm('Есть несохранённые изменения. Заменить их?');
  newDraft=function(...args){if(!discard(el('editorForm')))return;rawNew(...args);remember(el('editorForm'));recordChanged()};
  openDraft=async function(id){const form=el('editorForm');if(!discard(form))return;const before=snapshot(form);if(await rawOpen(id,()=>snapshot(form)===before||discard(form))){remember(form);recordChanged()}};
  resetTech=function(){if(!discard(el('techForm')))return;rawReset();remember(el('techForm'));recordChanged()};
  openTechDraft=function(d){if(!discard(el('techForm')))return;rawTechOpen(d);remember(el('techForm'));recordChanged()};
  openCreator=function(post){if(!discard(el('creatorForm')))return;rawCreator(post);remember(el('creatorForm'));recordChanged()};
  const rawAction=performAction;performAction=async function(action){if(dirty(el('editorForm'))){el('editorFeedback').replaceChildren(alertNode('Сначала сохрани изменения текста.','warning'));return}return rawAction(action)};
  // A successful save is the only request that clears the dirty state.
  const rawRequest=editorRequest;
  editorRequest=async function(url,method,payload){
    const formId=/\/api\/editor\/drafts(?:\/\d+)?$/.test(url)?'editorForm':/\/api\/tech\/drafts(?:\/\d+)?$/.test(url)?'techForm':/\/api\/creator\/posts\/\d+$/.test(url)?'creatorForm':null;
    const savedForm=formId&&['POST','PUT'].includes(method)?el(formId):null;
    const submitted=savedForm?snapshot(savedForm):null;
    const result=await rawRequest(url,method,payload);
    if(savedForm){snapshots.set(savedForm,submitted);document.dispatchEvent(new CustomEvent('editorsaved',{detail:{formId,id:result.id,created:method==='POST'}}))}
    return result;
  };
  for (const [formId,bodyId,titleId] of [['editorForm','editorBody',null],['techForm','techBody','techTitle'],['creatorForm','creatorNotes','creatorTitle']]) {
    const form=el(formId),body=el(bodyId);
    const panel=document.createElement('details');panel.className='writing-tools';
    panel.innerHTML='<summary>Перевод и SEO · инструменты текста</summary><p class="hint">SEO-поля сохраняются в этом браузере; скачай материал для переноса. Локальный перевод EN → RU. SEO-проверка — ориентир для статьи на сайте; ограничения каналов проверяются отдельно.</p><label class="editor-label">SEO-заголовок<input class="editor-input" data-field="title" maxlength="150"></label><label class="editor-label">Описание для поиска<textarea class="editor-input" data-field="description" rows="2" maxlength="320"></textarea></label><label class="editor-label">Ключевая фраза<input class="editor-input" data-field="keyword" maxlength="120"></label><div class="editor-buttons"><button type="button" data-action="check">Проверить SEO</button><button type="button" data-action="translate">Перевести текст EN → RU</button><button type="button" data-action="export">Скачать материал и SEO</button></div><div data-field="feedback" role="status"></div><div data-field="translation" hidden><label class="editor-label">Результат перевода<textarea class="editor-input" rows="8" data-field="result"></textarea></label><button type="button" class="editor-btn" data-action="apply">Вставить вместо текста</button></div>';
    form.after(panel);
    const field=name=>panel.querySelector('[data-field="'+name+'"]');
    const feedback=message=>field('feedback').replaceChildren(alertNode(message));
    // Keep SEO work between reloads, independently for each saved draft and project.
    function key(){const id=formId==='editorForm'?state.editorDraft?.id:formId==='techForm'?activeTech?.id:creatorSelected?.id;return 'content-hub:seo:'+formId+':'+(id||'new')}
    let currentKey='';
    function loadMetadata(){const next=key();if(next===currentKey)return;currentKey=next;let data={};try{data=JSON.parse(localStorage.getItem(next)||'{}')}catch{}
      for(const name of ['title','description','keyword'])field(name).value=data[name]||'';
      if(!field('title').value&&titleId)field('title').value=el(titleId).value;
      field('translation').hidden=true;field('feedback').replaceChildren();
    }
    function persist(){try{localStorage.setItem(key(),JSON.stringify(Object.fromEntries(['title','description','keyword'].map(n=>[n,field(n).value]))))}catch{feedback('SEO-поля не удалось сохранить в браузере. Скачай материал, чтобы сохранить их.')}}
    panel.addEventListener('input',persist);panel.addEventListener('toggle',loadMetadata);
    document.addEventListener('workspacechange',loadMetadata);
    document.addEventListener('editorrecordchange',loadMetadata);
    document.addEventListener('editorsaved',event=>{
      if(event.detail.formId!==formId||!event.detail.created)return;
      try{const next='content-hub:seo:'+formId+':'+event.detail.id;
        localStorage.setItem(next,JSON.stringify(Object.fromEntries(['title','description','keyword'].map(n=>[n,field(n).value]))));
        localStorage.removeItem('content-hub:seo:'+formId+':new');
      }catch{feedback('Не удалось сохранить SEO-поля. Скачай материал.')}
    });
    // Loading a draft may happen after the page is already visible.
    new MutationObserver(loadMetadata).observe(formId==='editorForm'?el('editorTitle'):el(formId==='techForm'?'techDrafts':'creatorCalendar'),{childList:true,subtree:true});
    panel.querySelector('[data-action="check"]').onclick=()=>{
      const title=field('title').value.trim(),description=field('description').value.trim(),keyword=field('keyword').value.trim().toLocaleLowerCase('ru'),text=body.value;
      const messages=[`Текст: ${text.length} символов, ${text.trim()?text.trim().split(/\s+/u).length:0} слов.`, `Заголовок: ${title.length} символов (ориентир 30–60).`, `Описание: ${description.length} символов (ориентир 120–160).`];
      if(keyword)messages.push(`Ключевая фраза: в заголовке ${title.toLocaleLowerCase('ru').includes(keyword)?'есть':'нет'}, в тексте ${text.toLocaleLowerCase('ru').includes(keyword)?'есть':'нет'}.`);
      feedback(messages.join(' '));persist();
    };
    panel.querySelector('[data-action="translate"]').onclick=async e=>{
      const source=body.value,sourceKey=key();if(!source.trim()){feedback('Сначала добавь текст.');return}
      const b=e.currentTarget;b.disabled=true;feedback('Переводим локально…');
      try{const result=await editorRequest('/api/editor/tools/translate','POST',{text:source});if(key()!==sourceKey)return;field('result').value=result.text;field('translation').hidden=false;field('translation').dataset.source=source;feedback('Проверь перевод перед вставкой. Исходный текст сохранён в редакторе.')}
      catch(error){feedback(error.message)}finally{b.disabled=false}
    };
    panel.querySelector('[data-action="apply"]').onclick=()=>{
      if(body.disabled||body.readOnly||(formId==='editorForm'&&el('editorSave').disabled)){feedback('Этот материал недоступен для редактирования.');return}
      const result=field('result').value;
      if(body.maxLength>0&&result.length>body.maxLength){feedback('Перевод превышает допустимую длину текста. Сократи его перед вставкой.');return}
      if(body.value!==field('translation').dataset.source&&!confirm('Текст изменился после перевода. Заменить текущую версию?'))return;
      body.value=result;body.dispatchEvent(new Event('input',{bubbles:true}));feedback('Перевод вставлен. Сохрани материал обычной кнопкой.');
    };
    panel.querySelector('[data-action="export"]').onclick=()=>{
      const data={title:field('title').value,description:field('description').value,keyword:field('keyword').value,text:body.value};
      const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='content-hub-material.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);persist();
    };
    loadMetadata();
  }
  showPage(location.hash.slice(1)||state.page);
})();

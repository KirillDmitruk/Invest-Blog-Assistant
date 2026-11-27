/* Native, keyboard-accessible disclosure shared by both demo trade histories. */
(()=>{
 const expanded=new Set();
 function date(value,epoch=false){if(value===null||value===undefined||value==='')return 'Не сохранено';const d=new Date(epoch?Number(value)*1000:value);return Number.isFinite(d.getTime())?d.toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})+' МСК':'Не сохранено'}
 function wrap(row,fields,key){
  const details=document.createElement('details');details.className=row.className+' paper-trade-details';Object.assign(details.dataset,row.dataset);details.open=expanded.has(key);
  const summary=document.createElement('summary'),heading=document.createElement('div'),arrow=document.createElement('span');heading.className='paper-trade-heading';while(row.firstChild)heading.append(row.firstChild);arrow.className='paper-trade-arrow';arrow.textContent='›';arrow.setAttribute('aria-hidden','true');summary.append(heading,arrow);summary.title='Раскрыть или свернуть детали сделки';
  const body=document.createElement('div');body.className='paper-fields paper-trade-body';for(const [label,value,kind] of fields){const cell=document.createElement('div'),name=document.createElement('span'),data=document.createElement('strong');name.textContent=label;data.textContent=value===null||value===undefined?'Не сохранено':String(value);if(kind)cell.className='paper-'+kind;cell.append(name,data);body.append(cell)}
  details.append(summary,body);details.addEventListener('toggle',()=>{if(details.open)expanded.add(key);else expanded.delete(key)});return details;
 }
 window.DemoTradeHistory={wrap,date};
})();

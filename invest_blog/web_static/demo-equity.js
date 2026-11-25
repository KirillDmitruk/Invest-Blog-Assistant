/* Persisted account estimates only. Shared by both PAPER dashboards. */
(() => {
 const NS='http://www.w3.org/2000/svg';
 function draw(host,rows,{budget,currency='RUB',color='#bda0ff',label='Динамика виртуального капитала'}={}){
  host.replaceChildren();const money=v=>Number(v).toLocaleString('ru-RU',{style:'currency',currency,maximumFractionDigits:2}),date=t=>new Date(t).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
  const values=new Map();for(const r of rows||[]){if(r?.equity==null||r?.at==null)continue;const t=typeof r.at==='number'?(r.at<1e12?r.at*1000:r.at):Date.parse(r.at),v=Number(r.equity);if(Number.isFinite(t)&&Number.isFinite(v))values.set(t,{t,v,partial:!!r.partial})}
  const points=[...values.values()].sort((a,b)=>a.t-b.t);if(points.length<2){const p=document.createElement('p');p.textContent='График появится после двух сохранённых оценок счёта.';host.append(p);return}
  budget=Number(budget);if(!Number.isFinite(budget))return;const lo=Math.min(...points.map(p=>p.v),budget),hi=Math.max(...points.map(p=>p.v),budget),pad=Math.max((hi-lo)*.15,Math.abs(budget)*.002,1),bottom=lo-pad,top=hi+pad;
  const x=t=>90+(t-points[0].t)/(points.at(-1).t-points[0].t)*505,y=v=>245-(v-bottom)/(top-bottom)*205;
  const node=(tag,attrs)=>{const e=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e};
  const svg=node('svg',{viewBox:'0 0 620 285',role:'img','aria-label':label});svg.classList.add('demo-equity-chart');
  for(const v of [bottom,(bottom+top)/2,top]){svg.append(node('line',{x1:90,x2:595,y1:y(v),y2:y(v),stroke:'#49516b','stroke-dasharray':'3 6'}));const t=node('text',{x:85,y:y(v)+4,fill:'#b9c5dc','font-size':13,'text-anchor':'end'});t.textContent=money(v);svg.append(t)}
  svg.append(node('line',{x1:90,x2:595,y1:y(budget),y2:y(budget),stroke:'#e6c77e','stroke-dasharray':'7 5'}));
  const d=points.map((p,i)=>(i?'L':'M')+x(p.t)+','+y(p.v)).join(' ');svg.append(node('path',{d:d+' L595,250 L90,250 Z',fill:color+'20'}),node('path',{d,fill:'none',stroke:color,'stroke-width':3,'stroke-linejoin':'round'}));
  // Titles show the stored time and amount without inventing values between samples.
  for(const p of points){const dot=node('circle',{cx:x(p.t),cy:y(p.v),r:points.length>80?1.5:3,fill:p.partial?'#ffc582':color});const title=node('title',{});title.textContent=date(p.t)+' МСК · '+money(p.v)+(p.partial?' · часть котировок не обновлена':'');dot.append(title);svg.append(dot)}
  for(const [t,anchor] of [[points[0].t,'start'],[points.at(-1).t,'end']]){const label=node('text',{x:x(t),y:277,fill:'#b9c5dc','font-size':13,'text-anchor':anchor});label.textContent=date(t)+' МСК';svg.append(label)}host.append(svg);
 }
 window.DemoEquityChart={draw};
})();

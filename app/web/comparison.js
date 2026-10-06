// Observed metadata only: this page never invokes scans, cleanup or AI.
const comparisonNav=document.createElement('button');
comparisonNav.className='nav-item';comparisonNav.textContent='⇄　历史扫描对比';
$('.sidebar nav').append(comparisonNav);
const comparisonPanel=document.createElement('section');
comparisonPanel.id='comparison';comparisonPanel.className='hidden';
comparisonPanel.innerHTML=`<div class="eyebrow">追踪空间变化 · 不修改源文件</div><h1>这次空间，变在哪里？</h1>
<p class="muted">只比较两份历史清单的路径和逻辑大小，不重新扫描。未再观察到不代表真的删除，同大小的内容变化不会检测。</p>
<section class="panel"><div class="row"><h2>选择同一根目录的两次扫描</h2><button id="comparison-refresh" class="btn secondary">刷新历史</button></div>
<form id="comparison-form"><div class="row"><label>基线 <select id="comparison-baseline" required aria-label="对比基线"></select></label><label>当前 <select id="comparison-current" required aria-label="对比当前"></select></label></div>
<label class="small">目录范围（留空为扫描根目录）<input id="comparison-path" maxlength="4096" placeholder="历史扫描内的目录路径" style="width:100%"></label>
<div class="row" style="margin-top:12px"><input id="comparison-query" class="grow" maxlength="200" placeholder="按路径文字筛选，不使用通配符" aria-label="对比搜索"><select id="comparison-kind" aria-label="变化类型"><option value="all">全部变化</option><option value="added">新增观察</option><option value="removed">未再观察到</option><option value="resized">大小变化</option></select><button class="btn" id="comparison-run">比较清单</button></div></form></section>
<section class="panel"><p id="comparison-summary" role="status">选择两次已完成的扫描后比较。</p><p id="comparison-warning" class="notice"></p><div class="row"><a id="comparison-export" class="btn secondary hidden">导出筛选结果 CSV</a><span class="spacer"></span><button id="comparison-prev" class="btn secondary" disabled>上一页</button><button id="comparison-next" class="btn secondary" disabled>下一页</button></div><div id="comparison-items"></div></section>`;
$('footer').before(comparisonPanel);
let comparisonRequest=0,comparisonOffset=0,comparisonParams=null;
const signedBytes=n=>(n<0?'−':n>0?'+':'')+bytes(Math.abs(n));
async function comparisonHistory(){
 const history=(await api('/scans')).filter(j=>j.finished_at&&['completed','partial'].includes(j.state)).sort((a,b)=>b.finished_at-a.finished_at||b.id.localeCompare(a.id));
 const old=$('#comparison-baseline').value,current=$('#comparison-current').value;
 const options=history.map(j=>`<option value="${esc(j.id)}">${esc(j.root)} · ${esc(new Date(j.finished_at*1000).toLocaleString())} · ${esc(j.id.slice(0,8))}</option>`).join('');
 $('#comparison-baseline').innerHTML=$('#comparison-current').innerHTML=options;
 $('#comparison-baseline').value=history.some(j=>j.id===old)?old:history[1]?.id||history[0]?.id||'';
 $('#comparison-current').value=history.some(j=>j.id===current)?current:history[0]?.id||'';
 $('#comparison-run').disabled=history.length<2;
 if(history.length<2)$('#comparison-summary').textContent='需要同一根目录的两份已完成历史清单；可以在空间分析中分别保存扫描。';
}
async function comparisonLoad(params,offset=0){
 const request=++comparisonRequest;
 $('#comparison-export').classList.add('hidden');$('#comparison-prev').disabled=$('#comparison-next').disabled=true;
 $('#comparison-summary').textContent='正在只读比较历史清单…';$('#comparison-items').innerHTML='';$('#comparison-warning').textContent='';
 try{
  const query=new URLSearchParams({...params,offset,limit:100});
  const data=await api('/comparisons?'+query);
  if(request!==comparisonRequest)return;
  comparisonParams={...params};comparisonOffset=offset;
  $('#comparison-summary').textContent=`${data.path} · ${data.total} 个变化 · 新增 ${data.counts.added} / 未再观察到 ${data.counts.removed} / 大小变化 ${data.counts.resized} · 筛选净变化 ${signedBytes(data.delta_bytes)} · 第 ${data.total?offset+1:0}–${Math.min(offset+data.items.length,data.total)} 项`;
  $('#comparison-warning').textContent=(data.warnings.length?data.warnings.join(' '):'两次扫描已完成可访问范围遍历；链接和排除目录不在比较范围内。')+' '+data.measurement;
  const labels={added:'新增观察',removed:'未再观察到',resized:'大小变化'};
  $('#comparison-items').innerHTML=data.items.length?`<div class="table-wrap"><table><thead><tr><th>文件路径</th><th>观察变化</th><th>基线</th><th>当前</th><th>差值</th></tr></thead><tbody>${data.items.map(i=>`<tr><td class="path small">${esc(i.path)}</td><td>${labels[i.kind]}</td><td>${i.baseline_size===null?'—':bytes(i.baseline_size)}</td><td>${i.current_size===null?'—':bytes(i.current_size)}</td><td>${signedBytes(i.delta_bytes)}</td></tr>`).join('')}</tbody></table></div>`:empty('筛选范围内没有已观察大小变化','相同大小不等于内容相同；此处不会读取或校验文件正文。');
  $('#comparison-prev').disabled=offset===0;$('#comparison-next').disabled=!data.has_more;
  $('#comparison-export').href='/api/comparisons.csv?'+new URLSearchParams(params);$('#comparison-export').classList.remove('hidden');
 }catch(error){if(request===comparisonRequest){$('#comparison-summary').textContent='无法比较：'+error.message;toast(error.message,true);}}
}
comparisonNav.onclick=guard(async()=>{$$('[data-view]').forEach(b=>b.classList.remove('active'));comparisonNav.classList.add('active');$('#analysis').classList.add('hidden');$('#history').classList.add('hidden');comparisonPanel.classList.remove('hidden');await comparisonHistory();});
$$('[data-view]').forEach(b=>b.addEventListener('click',()=>{comparisonPanel.classList.add('hidden');comparisonNav.classList.remove('active');}));
$('#comparison-refresh').onclick=guard(comparisonHistory);
$('#comparison-form').onsubmit=e=>{e.preventDefault();comparisonLoad({baseline:$('#comparison-baseline').value,current:$('#comparison-current').value,path:$('#comparison-path').value,q:$('#comparison-query').value,kind:$('#comparison-kind').value});};
$('#comparison-prev').onclick=()=>comparisonLoad(comparisonParams,Math.max(0,comparisonOffset-100));
$('#comparison-next').onclick=()=>comparisonLoad(comparisonParams,comparisonOffset+100);

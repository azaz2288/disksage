let scanId=null, result=null, timer=null, execute=null;
$('#directories').innerHTML=empty('先分析一个目录','扫描结束后展示目录占用排行');
$('#largest').innerHTML=empty('找出空间去向','大文件只展示，不进入自动清理');
$('#candidates').innerHTML=empty('建议以安全为先','扫描后仅列出符合保守规则的临时文件');
function render(r){
 result=r; $('#total').textContent=bytes(r.bytes);$('#count').textContent=r.files.toLocaleString();$('#safe').textContent=bytes(r.candidates.reduce((n,c)=>n+c.size,0));
 $('#directories').innerHTML=r.directories.slice(0,12).map(d=>`<div style="margin:15px 0"><div class="row"><span class="small path" style="flex:1">${esc(d.path)}</span><strong>${bytes(d.size)}</strong></div><div class="bar"><span style="width:${Math.max(1,d.size/Math.max(r.bytes,1)*100)}%"></span></div></div>`).join('')||empty('没有可读目录','检查访问权限');
 $('#largest').innerHTML=r.largest.slice(0,12).map(f=>`<div style="margin:15px 0"><strong>${bytes(f.size)}</strong><div class="small muted path">${esc(f.path)}</div></div>`).join('')||empty('没有可读文件','');
 $('#candidates').innerHTML=r.candidates.length?`<div class="table-wrap"><table><thead><tr><th>选择</th><th>临时文件</th><th>大小</th><th>距修改</th></tr></thead><tbody>${r.candidates.map(c=>`<tr><td><input type="checkbox" value="${c.id}" class="candidate"></td><td class="path">${esc(c.path)}</td><td>${bytes(c.size)}</td><td>${c.age_days} 天</td></tr>`).join('')}</tbody></table></div>`:empty('没有符合规则的临时文件','大文件与用户文档需要你自行检查，不会自动删除');
 $('#preview').disabled=!r.candidates.length;$('#ai').disabled=false;
}
async function poll(){
 const job=await api('/scans/'+scanId);const p=job.progress;
 $('#status').textContent=job.state==='running'?`正在分析 ${p.files||0} 个文件 · ${bytes(p.bytes||0)} · ${p.current||''}`:job.error||`分析${job.state==='partial'?'部分完成':'完成'} · 无权访问 ${p.errors||0} 项 · 链接已跳过`;
 if(job.state!=='running'){clearInterval(timer);$('#scan').disabled=false;$('#cancel').classList.add('hidden');$('#progress').classList.add('hidden');if(job.result)render(job.result);}
}
$('#scan-form').onsubmit=guard(async e=>{e.preventDefault();$('#scan').disabled=true;$('#answer').textContent='';try{const data=await post('/scans',{path:$('#path').value});scanId=data.id;$('#cancel').classList.remove('hidden');$('#progress').classList.remove('hidden');timer=setInterval(()=>guard(poll)(),700);await poll();}catch(e){$('#scan').disabled=false;throw e;}});
$('#cancel').onclick=guard(()=>post('/scans/'+scanId+'/cancel',{}));
function dialog(title,html,action,label){$('#confirm-title').textContent=title;$('#confirm-body').innerHTML=html;$('#execute').textContent=label;execute=action;$('#confirm').showModal();}
$('#close').onclick=()=>$('#confirm').close();
$('#execute').onclick=guard(async()=>{$('#execute').disabled=true;try{await execute();$('#confirm').close();}finally{$('#execute').disabled=false;}});
$('#preview').onclick=()=>{const ids=$$('.candidate:checked').map(c=>c.value);if(!ids.length)return toast('请先选择临时文件');const selected=result.candidates.filter(c=>ids.includes(c.id));dialog('确认隔离预览',`<p>将移动 ${ids.length} 个文件，共 ${bytes(selected.reduce((n,c)=>n+c.size,0))}。隔离后仍占用空间，可在记录中恢复。</p><div class="small path">${selected.map(c=>esc(c.path)).join('<br>')}</div>`,async()=>{const data=await post('/cleanup',{scan_id:scanId,ids,confirm:true});toast(`已隔离 ${data.items.filter(i=>i.state==='quarantined').length} 个文件`);render({...result,candidates:result.candidates.filter(c=>!ids.includes(c.id))});},'确认隔离');};
$('#ai').onclick=guard(async()=>{const summary=await api('/scans/'+scanId+'/ai-preview');dialog('发送给模型的内容',`<p>仅以下汇总，不含文件名和路径。</p><pre class="answer small">${esc(JSON.stringify(summary,null,2))}</pre>`,async()=>{const data=await post('/scans/'+scanId+'/advice',{});$('#answer').textContent=data.answer;},'发送摘要并分析');});
async function loadHistory(){const data=await api('/history');$('#history-list').innerHTML=data.length?data.map(r=>`<article style="padding:15px 0;border-bottom:1px solid var(--line)"><div class="row"><strong>${bytes(r.size)}</strong><span class="badge">${esc(r.state)}</span><span class="spacer"></span>${r.state==='quarantined'?`<button class="btn secondary restore" data-id="${r.id}">恢复</button>`:''}</div><p class="path small">${esc(r.source)}</p></article>`).join(''):empty('还没有隔离记录','所有清理操作都会记录在这里');$$('.restore').forEach(b=>b.onclick=guard(async()=>{await post('/history/'+b.dataset.id+'/restore',{});toast('文件已恢复');await loadHistory();}));}
$$('[data-view]').forEach(b=>b.onclick=guard(async()=>{$$('[data-view]').forEach(x=>x.classList.toggle('active',x===b));$('#analysis').classList.toggle('hidden',b.dataset.view!=='analysis');$('#history').classList.toggle('hidden',b.dataset.view!=='history');if(b.dataset.view==='history')await loadHistory();}));
guard(async()=>{const config=await api('/config');$('#path').value=config.default_path;$('#model').textContent=config.llm.configured?config.llm.model:'环境变量配置 API';})();

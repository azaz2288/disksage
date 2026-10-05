const inventoryControls=document.createElement('div');inventoryControls.className='row';inventoryControls.style.cssText='flex-wrap:wrap;margin:16px 0';
inventoryControls.innerHTML='<input id="file-query" placeholder="搜索文件名或路径" aria-label="搜索文件名或路径"><label><input type="checkbox" id="all-files"> 搜索整次扫描</label><select id="file-sort" aria-label="文件排序"><option value="size">逻辑大小</option><option value="allocated">实际分配</option><option value="name">名称</option><option value="modified">修改时间</option></select><button class="btn secondary" id="search-files">查询</button><a class="btn secondary" id="all-file-export">完整文件清单 CSV</a><button class="btn secondary" id="scan-errors">无法访问的项目</button>';
$('#browse-items').before(inventoryControls);
const inventoryPaging=document.createElement('div');inventoryPaging.className='row';inventoryPaging.innerHTML='<button class="btn secondary" id="file-prev">上一页</button><span class="small muted" id="file-page"></span><button class="btn secondary" id="file-next">下一页</button>';$('#browse-items').after(inventoryPaging);
let browsePath='',fileOffset=0;const pageSize=100;
browse=async(path='',offset=0)=>{
  const query=new URLSearchParams({path,q:$('#file-query').value,offset,limit:pageSize,recursive:$('#all-files').checked,sort:$('#file-sort').value});
  const data=await api('/scans/'+scanId+'/browse?'+query);browsePath=data.path;fileOffset=offset;browseParent=data.parent;
  $('#browse-path').textContent=data.path;$('#browse-up').disabled=!data.parent;
  $('#all-file-export').href='/api/scans/'+scanId+'/files.csv';
  $('#file-page').textContent=`${data.total?offset+1:0}–${Math.min(offset+data.items.length,data.total)} / ${data.total}项 · 全部已扫描文件均可翻页`;
  $('#file-prev').disabled=offset===0;$('#file-next').disabled=!data.has_more;
  $('#browse-items').innerHTML='<div class="table-wrap"><table><thead><tr><th>目录 / 文件</th><th>逻辑大小</th><th>实际分配</th><th>修改时间</th></tr></thead><tbody>'+data.items.map(i=>`<tr><td>${i.kind==='directory'?`<button class="btn secondary folder" data-path="${esc(i.path)}">▤ ${esc(i.name)}</button>`:`<span class="path small" title="${esc(i.path)}">${esc($('#all-files').checked?i.path:i.name)}</span>`}</td><td>${bytes(i.size)}</td><td>${i.allocated==null?'—':bytes(i.allocated)}</td><td class="small">${i.modified?esc(new Date(i.modified*1000).toLocaleString()):'—'}</td></tr>`).join('')+'</tbody></table></div>';
  $$('.folder').forEach(b=>b.onclick=guard(()=>browse(b.dataset.path)));
  const shown=data.items.filter(i=>i.size>0).slice(0,24),total=shown.reduce((n,i)=>n+i.size,0);
  mapBox.innerHTML=shown.map((i,n)=>`<button class="space-tile" style="flex:${Math.max(.1,i.size/Math.max(1,total)*100)};min-width:45px;border:0;color:white;background:hsl(${160+n*17} 35% 35%);padding:8px;overflow:hidden" data-kind="${i.kind}" data-path="${esc(i.path)}" title="${esc(i.path)}">${esc(i.name)}<br>${bytes(i.size)}</button>`).join('');
  $$('.space-tile').forEach(b=>b.onclick=guard(()=>b.dataset.kind==='directory'?browse(b.dataset.path):toast(b.dataset.path)));
};
$('#search-files').onclick=guard(()=>browse(browsePath));$('#file-query').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();guard(()=>browse(browsePath))();}};
$('#all-files').onchange=$('#file-sort').onchange=guard(()=>browse(browsePath));
$('#file-prev').onclick=guard(()=>browse(browsePath,Math.max(0,fileOffset-pageSize)));$('#file-next').onclick=guard(()=>browse(browsePath,fileOffset+pageSize));
$('#scan-errors').onclick=guard(async()=>{const data=await api('/scans/'+scanId+'/errors');dialog('扫描覆盖与访问失败',`<p>共 ${data.total} 个失败项目，显示前100项。权限不足的文件无法计算大小，需要用有权访问的账户重扫。</p>`+data.items.map(e=>`<p class="path small">${esc(e.path)}<br>${esc(e.reason)}</p>`).join(''),async()=>{},'关闭');});
const aiScope=document.createElement('div');aiScope.style.cssText='margin:15px 0';aiScope.innerHTML='<label class="small"><input id="ai-include-paths" type="checkbox"> 允许AI分析大文件名和目录路径（确认预览后发送给API服务商）</label><input id="ai-question" placeholder="例如：哪些目录值得优先检查？" maxlength="1000" style="width:100%;margin-top:12px">';$('#answer').before(aiScope);
$('#ai').onclick=guard(async()=>{const include=$('#ai-include-paths').checked,question=$('#ai-question').value||'请分析空间占用并给出保守清理建议';const summary=await api('/scans/'+scanId+'/ai-preview?include_paths='+include);dialog('本次发送给API的内容',`<p>${include?'包含文件名、目录路径和大小，不发送文件正文。':'只发送数量、大小和扩展名汇总，不含文件名、路径或正文。'}按API服务商规则计费。</p><pre class="answer small">${esc(JSON.stringify({question,scan:summary},null,2))}</pre>`,async()=>{const result=await post('/scans/'+scanId+'/advice',{include_paths:include,question});$('#answer').textContent=result.answer;},'确认发送并分析');});
const savedScans=document.createElement('select');savedScans.setAttribute('aria-label','历史扫描');savedScans.className='btn secondary';$('#scan-form').before(savedScans);
async function refreshSavedScans(){const data=await api('/scans');savedScans.innerHTML='<option value="">恢复历史扫描（已保存完整清单）</option>'+data.filter(j=>j.finished_at).map(j=>`<option value="${j.id}">${esc(j.root)} · ${esc(new Date(j.finished_at*1000).toLocaleString())}</option>`).join('');savedScans.value=scanId||'';}
savedScans.onchange=guard(async()=>{if(!savedScans.value)return;scanId=savedScans.value;await poll();if(result)$('#path').value=result.root;});
const inventoryRender=render;render=r=>{inventoryRender(r);$('#status').textContent=`${r.complete?'遍历结束':'扫描已取消或达到限额'} · ${r.files}文件 · ${r.errors}无法访问 · ${r.skipped}链接/隔离区/程序数据跳过 · ${r.coverage_complete?'可访问范围完整':'存在未扫描内容，请查看失败项目'} · 文件明细已保存`;guard(refreshSavedScans)();};
guard(refreshSavedScans)();

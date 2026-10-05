const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, options={}) {
  const response = await fetch('/api' + path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '操作失败，请检查输入');
  return body;
}
const post = (path, body) => api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
function toast(message, bad=false) {
  const el = $('#toast'); el.textContent = message; el.className = bad ? 'toast bad show' : 'toast show';
  clearTimeout(window.toastTimer); window.toastTimer = setTimeout(() => el.classList.remove('show'), 5000);
}
const bytes = (n) => { const units=['B','KB','MB','GB','TB']; let i=0; while(n>=1024 && i<4){n/=1024;i++;} return n.toFixed(i?1:0)+' '+units[i]; };
const guard = (fn) => async (...args) => {try {return await fn(...args);} catch(error){toast(error.message,true);}};
function empty(title, description) {return `<div class="empty"><div class="empty-icon">◇</div><h3>${esc(title)}</h3><p>${esc(description)}</p></div>`;}

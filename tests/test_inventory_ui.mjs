import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Exercise the actual browser browse function with controlled network ordering.
const source = readFileSync(new URL('../app/web/inventory.js', import.meta.url), 'utf8');
const browseSource = source.match(/browse=async\(path='',offset=0\)=>\{[\s\S]*?\n\};/)?.[0];
assert.ok(browseSource, 'browser browse entry point must be present');
function harness() {
  const nodes = new Map(), requests = [];
  const context = vm.createContext({URLSearchParams,
    $: selector => {
      if (!nodes.has(selector)) nodes.set(selector, {value:'', checked:false});
      return nodes.get(selector);
    },
    $$: () => [], mapBox: {}, jumpPage: {}, pageSize:100,
    esc: String, bytes: String,
    api: url => new Promise(resolve => requests.push({url,resolve})),
    scanId:'base', browseGeneration:0, browsePath:'initial', fileTotal:0,
  });
  vm.runInContext(browseSource, context);
  return {context, requests, nodes};
}
const reply = path => ({path,parent:'root',total:0,items:[],has_more:false});

test('latest directory response wins over older response', async () => {
  const {context,requests,nodes}=harness();
  const old=context.browse('old'), latest=context.browse('latest');
  requests[1].resolve(reply('latest')); await latest;
  requests[0].resolve(reply('old')); await old;
  assert.equal(context.browsePath,'latest');
  assert.equal(nodes.get('#browse-path').textContent,'latest');
});
test('history switch discards previous inventory response', async () => {
  const {context,requests,nodes}=harness();
  const pending=context.browse('selected');
  context.scanId='other';
  requests[0].resolve(reply('selected')); await pending;
  assert.equal(context.browsePath,'initial');
  assert.equal(nodes.has('#browse-path'),false);
  assert.match(requests[0].url,/^\/scans\/base\/browse\?/);
});
test('active inventory response updates scope and export together', async () => {
  const {context,requests,nodes}=harness();
  const pending=context.browse('selected');
  requests[0].resolve(reply('selected')); await pending;
  assert.equal(context.browsePath,'selected');
  assert.equal(nodes.get('#all-file-export').href,'/api/scans/base/files.csv');
});

const appSource=readFileSync(new URL('../app/web/app.js', import.meta.url),'utf8');
const pollSource=appSource.match(/async function poll\(\)\{[\s\S]*?\n\}/)?.[0];
const refreshSource=source.slice(source.indexOf('refreshDirectory.onclick='));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function refreshHarness(){
  const nodes=new Map(),requests=[],posts=[],renders=[];
  const context=vm.createContext({scanId:'base',browsePath:'root/selected',result:{root:'root'},refreshBaseline:null,
    refreshDirectory:{},timer:1,guard:f=>f,esc:String,bytes:String,toast:()=>{},
    $:s=>{if(!nodes.has(s))nodes.set(s,{value:'300',classList:{add(){},remove(){}}});return nodes.get(s);},
    api:url=>new Promise(resolve=>requests.push({url,resolve})),
    post:async(url,body)=>{posts.push({url,body});return {id:'new'};},
    render:r=>renders.push(r),clearInterval:()=>{},setInterval:()=>2,
    dialog:(_t,html,action)=>{context.preview=html;context.action=action;},
  });
  vm.runInContext(pollSource+'\n'+refreshSource,context);
  return {context,nodes,requests,posts,renders};
}
test('refresh confirmation validates strict budget and sends the selected scope',async()=>{
  const h=refreshHarness();await h.context.refreshDirectory.onclick();
  assert.match(h.context.preview,/refresh-budget/);
  for(const value of ['0','3601','1.5','']){
    h.context.$('#refresh-budget').value=value;
    await assert.rejects(h.context.action());assert.equal(h.posts.length,0);
  }
  h.context.$('#refresh-budget').value='9';
  const pending=h.context.action();await flush();
  assert.equal(h.posts[0].body.timeout_seconds,9);assert.equal(h.posts[0].body.path,'root/selected');
  h.requests[0].resolve({state:'running',progress:{}});await pending;
  assert.equal(h.context.refreshBaseline,'base');assert.equal(h.context.scanId,'new');
});
test('changed baseline invalidates refresh confirmation',async()=>{
  const h=refreshHarness();await h.context.refreshDirectory.onclick();h.context.scanId='other';
  await assert.rejects(h.context.action(),/已切换/);assert.equal(h.posts.length,0);
});
test('timeout and cancellation restore original baseline without automatic retry',async()=>{
  for(const state of ['timed_out','cancelled','failed']){
    const h=refreshHarness();h.context.scanId='new';h.context.refreshBaseline='base';
    const pending=h.context.poll();h.requests[0].resolve({state,progress:{},error:state});await flush();
    assert.equal(h.requests[1].url,'/scans/base');h.requests[1].resolve({result:{bytes:7}});await pending;
    assert.equal(h.context.scanId,'base');assert.equal(h.context.refreshBaseline,null);
    assert.equal(h.renders[0].bytes,7);assert.match(h.nodes.get('#status').textContent,/已恢复原基线/);
    assert.equal(h.posts.length,0);
  }
});
test('late polling and baseline recovery cannot overwrite another history view',async()=>{
  const h=refreshHarness();const p=h.context.poll();h.context.scanId='other';
  h.requests[0].resolve({state:'completed',progress:{},result:{bytes:999}});await p;assert.equal(h.renders.length,0);
  h.context.scanId='new';h.context.refreshBaseline='base';const retry=h.context.poll();
  h.requests[1].resolve({state:'timed_out',progress:{}});await flush();h.context.scanId='other';
  h.requests[2].resolve({result:{bytes:7}});await retry;assert.equal(h.context.scanId,'other');assert.equal(h.renders.length,0);
});

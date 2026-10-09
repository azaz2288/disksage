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

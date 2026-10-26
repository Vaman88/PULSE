const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');

test('pair every disease horizon, default to one-week sorting, reject incomplete pairs', () => {
  const source = readFileSync(__dirname + '/live.js', 'utf8');
  const pretty = source.slice(source.indexOf('const prettyDisease'), source.indexOf('let docsLoaded'));
  const grouping = source.slice(source.indexOf('function scoreList'), source.indexOf('function renderNews'));
  const group = runInNewContext(pretty + grouping + '; scoreList');
  const outputs = Object.fromEntries(Array.from({length:14}, (_,i) => [
    [`disease_${i}_2w`, i === 1 ? 10 : 2], [`disease_${i}_1w`, i === 0 ? 8 : 1]
  ]).flat());
  const rows = JSON.parse(JSON.stringify(group(outputs)));
  assert.equal(rows.length, 14);
  assert.deepEqual(rows[0], {disease:'disease 0', week1:8, week2:2});
  for (const row of rows) {
    const key = row.disease.replaceAll(' ', '_');
    assert.equal(row.week1, outputs[key + '_1w']);
    assert.equal(row.week2, outputs[key + '_2w']);
  }
  const incomplete = {...outputs, orphan_2w:2};
  delete incomplete.disease_0_2w;
  assert.throws(() => group(incomplete), /both horizons/);
  assert.throws(() => group({...outputs, disease_0_1w:11}), /1–10 scale/);
});

test('workspace upload validates CSV type, empty files, and the exact server size limit', () => {
  const source = readFileSync(__dirname + '/live.js', 'utf8');
  const validation = source.slice(source.indexOf('function uploadError'), source.indexOf('function renderPharmacy'));
  const error = runInNewContext(validation + '; uploadError');
  assert.equal(error({name:'sales.CSV',size:5_000_000}), '');
  assert.match(error({name:'sales.csv',size:5_000_001}), /exceeds 5 MB/);
  assert.match(error({name:'sales.csv',size:0}), /empty/);
  assert.match(error({name:'sales.csv.exe',size:100}), /Choose a CSV/);
  assert.match(error(), /Choose a file/);
});

test('upload feedback enables valid files, locks during processing, and recovers from server errors', async () => {
  const source = readFileSync(__dirname + '/live.js', 'utf8');
  const upload = source.slice(source.indexOf('function stockAtRisk'), source.indexOf('const tour ='));
  const fieldset = {disabled:false}, button = {disabled:true}, status = {dataset:{}};
  const label = {classList:{toggle() {}}};
  const input = {files:[], closest:() => label};
  const attributes = new Set();
  const form = {csv:input, querySelector:selector => selector === 'fieldset' ? fieldset : button,
    setAttribute:name => attributes.add(name), removeAttribute:name => attributes.delete(name)};
  const nodes = {'#pharmacy-upload':form, '#pharmacy-logout':{}, '#upload-status':status, '#workspace-news':{isConnected:true},
    '#upload-filename':{}, '#upload-filedetail':{}};
  let success = false;
  const render = runInNewContext(upload + '; renderPharmacy', {
    $:selector => nodes[selector], escapeHTML:String, signOut() {}, workspaceRequest:1,
    api:async () => ({articles:[]}), renderNews:() => '',
    fetch:async (url, options) => {
      assert.equal(url, '/api/v1/plan');
      assert.equal(options.body, 'sample CSV');
      assert.equal(options.headers['Content-Type'], 'text/csv');
      assert.equal(fieldset.disabled, true);
      assert.equal(attributes.has('aria-busy'), true);
      return {ok:success, json:async () => success ? {generated_at:'2026-10-25T12:00:00Z',recommendations:[]} : {detail:'Not enough matching dates'}};
    }
  });
  const element = {};
  render(element, {recommendations:[]});
  input.files = [{name:'wrong.xlsx', size:100}];
  input.onchange();
  assert.equal(button.disabled, true);
  assert.equal(status.dataset.state, 'error');
  input.files = [{name:'sales.csv',size:100,text:async () => 'sample CSV'}];
  input.onchange();
  assert.equal(button.disabled, false);
  assert.equal(nodes['#upload-filename'].textContent, 'sales.csv');
  let prevented = false;
  await form.onsubmit({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(status.textContent, 'Not enough matching dates');
  assert.equal(status.dataset.state, 'error');
  assert.equal(fieldset.disabled, false);
  assert.equal(attributes.has('aria-busy'), false);
  success = true;
  await form.onsubmit({preventDefault() {}});
  assert.match(element.innerHTML, /id="workspace-upload"[^>]*hidden/);
  assert.match(element.innerHTML, /Reupload data/);
  assert.ok(element.innerHTML.indexOf('id="workspace-news"') < element.innerHTML.indexOf('id="inventory-risk"'));
});

test('workspace prioritizes on-hand shortages, then purchase quantities, and renders real history', () => {
  const source = readFileSync(__dirname + '/live.js', 'utf8');
  const helpers = source.slice(source.indexOf('function stockAtRisk'), source.indexOf('async function loadWorkspaceNews'));
  const {purchaseOrder, inventoryChart} = runInNewContext(helpers + '; ({purchaseOrder, inventoryChart})', {escapeHTML:String});
  const rows = [
    {drug_name:'Plenty',current_stock:100,demand_7d:90,buy_7d:500,buy_14d:700},
    {drug_name:'Less urgent',current_stock:1,demand_7d:10,buy_7d:4,buy_14d:30},
    {drug_name:'First',current_stock:5,demand_7d:25,buy_7d:20,buy_14d:50},
    {drug_name:'Second',current_stock:5,demand_7d:25,buy_7d:20,buy_14d:40},
    {drug_name:'Unknown',current_stock:null,demand_7d:25,buy_7d:null,buy_14d:null}
  ];
  assert.deepEqual(rows.sort(purchaseOrder).map(r=>r.drug_name), ['First','Second','Less urgent','Plenty','Unknown']);
  const chart = inventoryChart({...rows[0],unit:'tablets',as_of:'2026-10-24',
    history:[{date:'2026-10-22',stock:12,sales:4},{date:'2026-10-24',stock:5,sales:7}],
    daily_forecasts:[{date:'2026-10-25',demand:3.5},{date:'2026-11-07',demand:3.5}]});
  assert.match(chart, /closing stock 12 tablets/);
  assert.match(chart, /sold 7 tablets/);
  assert.match(chart, /estimated demand 3.5 tablets/);
  assert.doesNotMatch(chart, /NaN|Infinity/);
  assert.match(inventoryChart({...rows[0],unit:'tablets'}), /Reupload data/);
});

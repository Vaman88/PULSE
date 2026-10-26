const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const prettyDisease = key => key.replace(/_([12])w$/, '').replaceAll('_', ' ');
let docsLoaded = false;
async function api(path) {
  const response = await fetch('/api/v1' + path, {headers:{Accept:'application/json'}});
  const data = await response.json();
  if (!response.ok) throw Error(data.detail || 'The API request failed');
  return data;
}
let currentPage = 'home', backStack = [], signedIn = false, workspaceRequest = 0;
const pages = {home:'Home', workspace:'Workspace', signals:'Signals', api:'API', access:'Sign in', privacy:'Privacy', terms:'Use & limitations'};
function updateNavigation() {
  const active = $('.nav button.active'), indicator = $('.nav-indicator');
  indicator.style.opacity = active ? '1' : '0';
  if (active) { indicator.style.width = active.offsetWidth + 'px'; indicator.style.transform = `translateX(${active.offsetLeft}px)`; }
  $('#nav-back').disabled = !backStack.length;
  $('#nav-next-label').textContent = pages[nextPage()];
  $('#nav-next').setAttribute('aria-label', 'Next: ' + pages[nextPage()]);
  document.documentElement.style.setProperty('--header-height', $('.top').offsetHeight + 'px');
}
function nextPage() { return {home:'workspace',workspace:'signals',signals:'api',api:signedIn?'workspace':'access',access:'workspace'}[currentPage] || 'home'; }
function show(page, back=false) {
  page = page === 'pharmacy' ? 'workspace' : page === 'docs' ? 'api' : page;
  if (!pages[page]) page = 'home';
  if (page !== currentPage && !back) backStack.push(currentPage);
  currentPage = page;
  workspaceRequest++;
  document.querySelectorAll('.screen').forEach(node => node.classList.toggle('active', node.id === page));
  document.querySelectorAll('.nav [data-screen]').forEach(node => {
    node.classList.toggle('active', node.dataset.screen === page);
    if (node.dataset.screen === page) node.setAttribute('aria-current','page'); else node.removeAttribute('aria-current');
  });
  if (page === 'signals') loadSignals();
  if (page === 'api' && !docsLoaded) loadDocs();
  if (page === 'workspace' || page === 'access') loadPharmacy();
  history.replaceState(null, '', '#' + page);
  scrollTo(0, 0);
  updateNavigation();
  if (typeof syncTour === 'function') syncTour();
}
document.addEventListener('click', event => {
  const node = event.target.closest('[data-go], [data-screen], [data-footer-home]');
  if (!node) return;
  event.preventDefault();
  if (node.dataset.screen === 'access' && signedIn) { signOut(); return; }
  show(node.dataset.footerHome ? 'home' : node.dataset.go || node.dataset.screen);
  if (node.dataset.footerHome) document.getElementById(node.dataset.footerHome)?.scrollIntoView();
});
window.addEventListener('hashchange', () => { if (pages[location.hash.slice(1)] || ['pharmacy','docs'].includes(location.hash.slice(1))) show(location.hash.slice(1)); });
$('#nav-back').onclick = () => { if (backStack.length) show(backStack.pop(), true); };
$('#nav-next').onclick = () => show(nextPage());
new ResizeObserver(updateNavigation).observe($('.top'));
function setSignedIn(value) { signedIn = value; $('.nav [data-screen="access"]').textContent = value ? 'Sign out' : 'Sign in'; updateNavigation(); }
async function signOut() {
  try {
    const response = await fetch('/api/v1/logout', {method:'POST'});
    if (!response.ok && response.status !== 401) throw Error('Sign out failed. Try again.');
    setSignedIn(false); $('#pharmacy-content').replaceChildren(); show('access');
  } catch (error) { alert(error.message); }
}
function scoreList(outputs) {
  if (!outputs || Object.keys(outputs).length !== 28) throw Error('This completed run does not contain exactly 28 final signals.');
  const rows = Object.entries(outputs);
  if (rows.some(([,score]) => !Number.isInteger(score) || score < 1 || score > 10)) throw Error('The API returned a score outside the documented 1–10 scale.');
  const diseases = rows.filter(([name]) => name.endsWith('_1w')).map(([name,week1]) => ({disease:prettyDisease(name),week1,week2:outputs[name.replace(/_1w$/, '_2w')]}));
  if (diseases.length !== 14 || diseases.some(row => !Number.isInteger(row.week2))) throw Error('This run does not contain both horizons for every disease.');
  return diseases.sort((a,b) => b.week1-a.week1 || a.disease.localeCompare(b.disease));
}
function sortSignals(button) {
  if (button.getAttribute('aria-pressed') === 'true') return;
  const table = button.closest('table'), body = table.tBodies[0], rows = [...body.rows];
  const positions = new Map(rows.map(row => [row, row.getBoundingClientRect().top]));
  rows.forEach(row => row.getAnimations().forEach(animation => animation.cancel()));
  rows.sort((a,b) => Number(b.dataset[button.dataset.sort])-Number(a.dataset[button.dataset.sort]) || a.cells[0].textContent.localeCompare(b.cells[0].textContent));
  rows.forEach(row => body.append(row));
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) rows.forEach(row => {
    const distance = positions.get(row)-row.getBoundingClientRect().top;
    if (distance) row.animate([{transform:`translateY(${distance}px)`},{transform:'translateY(0)'}], {duration:450,easing:'cubic-bezier(.22,1,.36,1)'});
  });
  table.querySelectorAll('[data-sort]').forEach(toggle => {
    const active = toggle === button;
    toggle.setAttribute('aria-pressed', String(active));
    toggle.closest('th').setAttribute('aria-sort', active ? 'descending' : 'none');
  });
  $('#outlook-sort').textContent = button.dataset.sort === 'week1' ? '1-week' : '2-week';
}
function renderNews(articles) {
  if (!articles?.length) return '<p class="status">No relevant articles matched in the seven days before the latest execution.</p>';
  return `<div class="news-grid">${[...articles].sort((a,b) => b.date.localeCompare(a.date)).map(article => `<article class="article"><small><time datetime="${escapeHTML(article.date)}">${escapeHTML(article.date)}</time> · ${escapeHTML(article.source)}</small><a href="${escapeHTML(article.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(article.title)}</a><small>Matched: ${article.diseases.map(escapeHTML).join(', ')}</small></article>`).join('')}</div>`;
}
async function loadSignals() {
  const container = $('#signals-content');
  container.innerHTML = '<p class="status" role="status">Loading latest signals…</p>';
  try {
    const [latest, news] = await Promise.all([api('/signals/latest'), api('/news/relevant')]);
    const signals = scoreList(latest.outputs);
    container.innerHTML = `<p class="eyebrow">PULSE / daily signals</p><h1 style="font-size:clamp(46px,6vw,72px)">Daily signals.</h1><p class="run-date">last model run : ${escapeHTML((latest.executed_at || latest.date).slice(0,10))}</p><p class="muted">Scores are model ratings. They are not probabilities, percentages, or confirmed outbreak predictions.</p><button class="rubric-trigger" type="button" aria-haspopup="dialog">See how scores work</button>
      <h2 style="margin-top:48px">Relevant news</h2><p class="muted">Articles collected by the backend in the seven days before this run, matched to disease names or documented synonyms in the title and article text.</p>${renderNews(news.articles)}
      <h2 style="margin-top:48px" id="outlook-heading">Disease outlook</h2><p class="muted" id="outlook-description">Compare both horizons side by side. Sorted by <span id="outlook-sort" aria-live="polite">1-week</span> rating, highest first. Scale: 1 (lowest) to 10 (highest).</p><div class="outlook"><table class="outlook-table" aria-labelledby="outlook-heading" aria-describedby="outlook-description"><thead><tr><th scope="col">Disease</th><th scope="col" aria-sort="descending"><div class="horizon-sort">1 week<button class="sort-toggle" type="button" data-sort="week1" aria-label="Sort by 1 week" aria-pressed="true"><span aria-hidden="true"></span></button></div></th><th scope="col" aria-sort="none"><div class="horizon-sort">2 weeks<button class="sort-toggle" type="button" data-sort="week2" aria-label="Sort by 2 weeks" aria-pressed="false"><span aria-hidden="true"></span></button></div></th></tr></thead><tbody>${signals.map(row => `<tr data-week1="${row.week1}" data-week2="${row.week2}"><th scope="row">${escapeHTML(row.disease)}</th>${[row.week1,row.week2].map(value => `<td><span class="signal-value" style="--score:${value*10}%"><b>${value}</b><span> / 10</span></span></td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    container.querySelector('.outlook-table').onclick = event => { const button = event.target.closest('[data-sort]'); if (button) sortSignals(button); };
    container.querySelector('.rubric-trigger').onclick = () => $('#score-rubric').showModal();
  } catch (error) {
    container.innerHTML = `<p class="status" role="status">${escapeHTML(error.message)}. Try again when a completed model execution is available.</p>`;
  }
}
function code(value) { return `<pre class="code"><code>${escapeHTML(JSON.stringify(value, null, 2))}</code></pre>`; }
async function loadDocs() {
  const el = $('#api-content');
  el.innerHTML = '<p class="status">Loading live API examples…</p>';
  try {
    const [latest, freshness] = await Promise.all([api('/signals/latest'), api('/signals/freshness')]);
    const history = await api(`/signals/history?start=${encodeURIComponent(latest.date)}&end=${encodeURIComponent(latest.date)}`);
    docsLoaded = true;
    el.innerHTML = `<div class="panel" style="background:var(--dark);color:white"><p class="eyebrow" style="color:#afc1bf">PULSE / API reference</p><h1 style="font-size:clamp(42px,6vw,66px)">Signals API.</h1><p class="lead" style="color:#d0e2df">Live examples below are taken from the current backend response.</p></div>
      <div class="actions"><a class="btn" href="/docs" target="_blank" rel="noopener">Interactive API reference ↗</a><a class="btn secondary" href="/openapi.json">OpenAPI JSON</a></div><section class="api-block"><h2>Workspace API</h2><p>Create an account with <code>POST /api/v1/auth/signup</code> or sign in with <code>POST /api/v1/auth/login</code>, sending JSON username and password. Signup requires at least 12 password characters. The server sets an HttpOnly session cookie.</p><p><code>GET /api/v1/plan</code> reads your saved plan; <code>POST /api/v1/plan</code> accepts raw text/csv, maximum 5 MB. Required columns: date, drug_name, unit, starting_stock, quantity_sold. Optional: ending_stock, current_stock, expected_delivery. <code>POST /api/v1/logout</code> ends the session. Private routes return 401 without a valid session.</p><p><code>GET /api/v1/news/relevant</code> returns matched articles with source dates. Public signal routes require no sign-in. See the interactive reference for request schemas and errors.</p></section><section class="api-block"><h2>Scoring contract</h2><p>Every completed prototype2 execution returns exactly 28 final signal scores, each an integer from 1 through 10. The fields are 14 diseases × two horizons. The API exposes the final <code>outputs</code> only; internal classifier counts and intermediate model outputs are not extra predictions. Scores are research model ratings, not probabilities or confirmed outbreak forecasts.</p></section>
      <section class="api-block"><h3>Latest signals</h3><p><code>GET /api/v1/signals/latest</code></p><p>Returns the most recent completed output record.</p>${code({request:'GET /api/v1/signals/latest',response:latest})}</section>
      <section class="api-block"><h3>Historical signals</h3><p><code>GET /api/v1/signals/history?start=YYYY-MM-DD&amp;end=YYYY-MM-DD</code></p><p>Inclusive date range; maximum range is 10 years. Dates without a completed prediction are omitted from <code>records</code>.</p>${code({request:`GET /api/v1/signals/history?start=${latest.date}&end=${latest.date}`,response:history})}</section>
      <section class="api-block"><h3>Execution metadata</h3><p><code>GET /api/v1/signals/freshness</code></p><p>Reports the completed prediction date and execution timestamp, plus source dates.</p>${code({request:'GET /api/v1/signals/freshness',response:freshness})}</section>`;
  } catch (error) {
    docsLoaded = false;
    el.innerHTML = `<p class="eyebrow">PULSE / API reference</p><h1 style="font-size:clamp(42px,6vw,66px)">Signals API.</h1><p class="lead">Every completed prototype2 execution returns exactly 28 final integer scores, each on a 1–10 scale. The API exposes final outputs only; scores are model ratings, not probabilities or confirmed outbreak forecasts.</p><p class="status">Live response examples are unavailable: ${escapeHTML(error.message)}. The endpoint contracts are listed below; examples appear when the backend has a completed run.</p><section class="api-block"><h3>Latest signals</h3><p><code>GET /api/v1/signals/latest</code> · One latest record: <code>{sample_id,date,outputs,disease_frequency,article_diseases,model,executed_at?}</code></p></section><section class="api-block"><h3>Historical signals</h3><p><code>GET /api/v1/signals/history?start=YYYY-MM-DD&amp;end=YYYY-MM-DD</code> · <code>{start,end,count,records}</code></p></section><section class="api-block"><h3>Execution metadata</h3><p><code>GET /api/v1/signals/freshness</code> · <code>{model,latest_prediction_date,latest_execution_at,news_date,fda_record_date,fda_carried_forward}</code></p></section>`;
  }
}
async function loadPharmacy(message='') {
  const el = $('#pharmacy-content'), request = ++workspaceRequest;
  try { const plan = await api('/plan'); if (request !== workspaceRequest) return; setSignedIn(true); if (currentPage === 'access') { show('workspace', true); return; } renderPharmacy(el, plan, message); }
  catch (e) { if (request !== workspaceRequest) return; if (e.message.includes('Sign in required')) { setSignedIn(false); el.replaceChildren(); if (currentPage === 'workspace') { show('access', true); return; } renderAuth($('#access-content'), message); } else (currentPage === 'access' ? $('#access-content') : el).innerHTML = `<p class="status">${escapeHTML(e.message)}</p>`; }
}
function renderAuth(el, message='') {
  el.innerHTML = `<p class="eyebrow">PULSE / private pharmacy research</p><h1 style="font-size:clamp(42px,6vw,68px)">Build your demand plan.</h1><p class="lead">Create an account or sign in. Uploaded CSV files are processed in memory and discarded.</p>${message?`<p class="status">${escapeHTML(message)}</p>`:''}<form id="pharmacy-auth"><label class="field">Username<input name="username" required minlength="3" maxlength="40" autocomplete="username"></label><label class="field">Password<input name="password" required type="password" autocomplete="current-password"></label><p class="muted">Use 12 or more characters when creating an account.</p><button class="btn" value="login">Sign in</button><button class="btn secondary" value="signup">Create account</button></form>`;
  $('#pharmacy-auth').addEventListener('submit', async ev => { ev.preventDefault(); const f=ev.currentTarget, action=ev.submitter.value; try { const r=await fetch(`/api/v1/auth/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:f.username.value,password:f.password.value})}); const d=await r.json(); if(!r.ok) throw Error(d.detail||'Authentication failed'); setSignedIn(true); $('#access-content').replaceChildren(); show('workspace'); } catch(e){ renderAuth(el,e.message); } });
}
function stockAtRisk(item) { return item.current_stock != null && item.demand_7d > item.current_stock; }
function purchaseOrder(a, b) {
  return Number(stockAtRisk(b))-Number(stockAtRisk(a)) || (b.buy_7d ?? -1)-(a.buy_7d ?? -1) || (b.buy_14d ?? -1)-(a.buy_14d ?? -1) || a.drug_name.localeCompare(b.drug_name);
}
function inventoryChart(item) {
  const history = item.history || [], future = item.daily_forecasts || [];
  const unit = escapeHTML(item.unit), name = escapeHTML(item.drug_name);
  const figures = `<div class="inventory-figures"><div>On hand<strong>${item.current_stock ?? 'Unknown'} <small>${unit}</small></strong></div><div>Incoming<strong>${item.expected_delivery ?? 0} <small>${unit}</small></strong></div><div>7-day demand<strong>${item.demand_7d} <small>${unit}</small></strong></div><div>Purchase estimate · 7 days<strong>${item.buy_7d ?? 'Conditional'} <small>${unit}</small></strong></div></div>`;
  if (!history.length) return `<h3>${name}</h3>${figures}<p class="status">This older plan has no saved stock/sales history. Reupload data to add the past-month chart.</p>`;
  const asOf = item.as_of || history.at(-1).date;
  const day = value => Date.parse(value + 'T00:00:00Z') / 86400000;
  const start = day(asOf)-29, x = value => 54+(day(value)-start)*682/43;
  const stockMax = Math.ceil(Math.max(1,...history.map(p=>p.stock))/10)*10;
  const salesMax = Math.ceil(Math.max(1,...history.map(p=>p.sales),...future.map(p=>p.demand))/10)*10;
  const stockY = value => 158-value/stockMax*105, salesY = value => 310-value/salesMax*90;
  const path = (points,key,y) => points.map((p,i)=>`${!i || day(p.date)-day(points[i-1].date)>1 ? 'M' : 'L'}${x(p.date).toFixed(1)},${y(p[key]).toFixed(1)}`).join(' ');
  const divider = x(asOf)+682/86;
  const ticks = (maximum,y) => [0,maximum/2,maximum].map(value=>`<line class="inventory-grid" x1="54" x2="736" y1="${y(value)}" y2="${y(value)}"/><text x="44" y="${y(value)+4}" text-anchor="end">${value}</text>`).join('');
  const from = new Date(start*86400000).toISOString().slice(0,10), until = future.at(-1)?.date || asOf;
  return `<div class="inventory-card-title"><div><p class="eyebrow">Needs attention · ${escapeHTML(asOf)}</p><h3>${name}</h3></div><span class="attention-tag">Demand exceeds on-hand stock</span></div>${figures}
    <figure class="inventory-figure"><div class="inventory-legend"><span class="legend-stock">Closing stock</span><span class="legend-sales">Daily sales</span><span class="legend-demand">Estimated daily demand</span></div>
    <div class="inventory-chart-scroll" tabindex="0" role="region" aria-label="Stock, sales and demand chart; scroll horizontally on small screens"><svg class="inventory-chart" viewBox="0 0 760 355" role="img" aria-label="${name}: past 30 days of closing stock and daily sales, followed by 14 days of estimated daily demand, in ${unit}">
    <rect x="${divider}" y="35" width="${736-divider}" height="290" fill="#f8f1e6" rx="6"/><line class="forecast-divider" x1="${divider}" x2="${divider}" y1="35" y2="325"/>
    <text x="54" y="24">Stock · ${unit}</text><text x="${divider+12}" y="24" class="forecast-label">14-day outlook</text>
    ${ticks(stockMax,stockY)}<path class="inventory-stock" d="${path(history,'stock',stockY)}"/>
    ${history.map(p=>`<circle class="inventory-point" cx="${x(p.date)}" cy="${stockY(p.stock)}" r="3"><title>${escapeHTML(p.date)}: closing stock ${p.stock} ${unit}</title></circle>`).join('')}
    <text x="${divider+12}" y="105" class="inventory-quiet">Future stock not estimated</text>
    <text x="54" y="199">Daily sales / demand · ${unit} per day</text>${ticks(salesMax,salesY)}
    ${history.map(p=>`<rect class="inventory-sales" x="${x(p.date)-3.5}" y="${salesY(p.sales)}" width="7" height="${310-salesY(p.sales)}" rx="2"><title>${escapeHTML(p.date)}: sold ${p.sales} ${unit}</title></rect>`).join('')}
    <path class="inventory-demand" d="${path(future,'demand',salesY)}"/>
    ${future.map(p=>`<circle class="demand-point" cx="${x(p.date)}" cy="${salesY(p.demand)}" r="3"><title>${escapeHTML(p.date)}: estimated demand ${p.demand} ${unit}</title></circle>`).join('')}
    <text x="54" y="343">${from}</text><text x="${x(asOf)}" y="343" text-anchor="middle">${escapeHTML(asOf)}</text><text x="736" y="343" text-anchor="end">${escapeHTML(until)}</text></svg></div>
    <figcaption>Observed history and estimated demand use separate scales. Missing history dates remain gaps. Forecasts hold the latest estimated daily rate; future sales and stock are not observations.</figcaption></figure>`;
}
async function loadWorkspaceNews() {
  const request = workspaceRequest, container = $('#workspace-news');
  try {
    const news = await api('/news/relevant');
    if (request === workspaceRequest && container.isConnected) container.innerHTML = renderNews(news.articles);
  } catch (error) {
    if (request === workspaceRequest && container.isConnected) container.textContent = 'News is unavailable right now. Your saved plan is still available.';
  }
}
function uploadError(file) {
  if (!file) return 'Choose a file to get started.';
  if (!/\.csv$/i.test(file.name)) return 'Choose a CSV file.';
  if (file.size > 5_000_000) return 'This file exceeds 5 MB. Choose a smaller CSV.';
  if (!file.size) return 'This file is empty. Choose a CSV with sales history.';
  return '';
}
function renderPharmacy(el, plan, message='') {
  const rec = [...(plan.recommendations || [])].sort(purchaseOrder), risk = rec.filter(stockAtRisk);
  const hasPlan = !!plan.generated_at || rec.length > 0;
  const rows = rec.map(r => {
    const attention = stockAtRisk(r), riskIndex = risk.indexOf(r);
    return `<tr${attention ? ' class="purchase-attention"' : ''}><th scope="row">${attention ? `<button class="medicine-link" type="button" data-risk-index="${riskIndex}">${escapeHTML(r.drug_name)}</button>` : `<strong>${escapeHTML(r.drug_name)}</strong>`}<small>${escapeHTML(r.unit)}</small>${attention ? '<span class="list-status">Demand exceeds on-hand</span>' : ''}</th><td>${r.current_stock ?? 'Not supplied'}</td><td>${r.expected_delivery ?? 0}</td><td>${r.demand_7d}</td><td>${r.demand_14d}</td><td class="purchase-value">${r.buy_7d ?? 'Conditional'}</td><td>${r.buy_14d ?? 'Conditional'}</td></tr>`;
  }).join('');
  el.innerHTML = `<div class="workspace-heading"><div><p class="eyebrow">Your pharmacy workspace</p><h1>${hasPlan ? 'Demand &amp;<br>inventory' : 'A clearer plan<br>for local care.'}</h1><p class="muted">${hasPlan ? 'Review the medicines that need attention before your next order.' : 'Bring your sales and stock together to build your first demand plan.'}</p></div><div class="workspace-actions">${hasPlan ? '<button id="reupload-data" class="btn" type="button" aria-expanded="false" aria-controls="workspace-upload">Reupload data</button>' : ''}<button id="pharmacy-logout" class="btn secondary" type="button">Sign out</button></div></div>
    ${message ? `<p class="status" role="status">${escapeHTML(message)}</p>` : ''}
    ${hasPlan ? `<div class="stats"><div class="stat">Medications<strong>${rec.length}</strong></div><div class="stat">Need attention<strong>${risk.length}</strong></div><div class="stat">Demand outlook<strong>7 / 14 days</strong></div><div class="stat">Plan updated<strong>${escapeHTML(plan.generated_at?.slice(0,10) || 'Not recorded')}</strong></div></div>` : ''}
    <section class="workspace-section workspace-news-section" aria-labelledby="workspace-news-title"><div class="workspace-section-head"><div><p class="eyebrow">Public health context</p><h2 id="workspace-news-title">Relevant news</h2></div><a href="#signals" data-go="signals" class="workspace-text-link">All public signals ↗</a></div><p class="muted">Recent disease-related reporting. These articles provide context, not proof of demand for a particular medicine.</p><div id="workspace-news" role="status"><p class="muted">Loading recent reporting…</p></div></section>
    <section class="upload workspace-upload" id="workspace-upload" aria-labelledby="upload-title" ${hasPlan ? 'hidden' : ''}>
<div class="upload-intro"><p class="eyebrow">Sales &amp; inventory</p><h2 id="upload-title">Start with your sales history.</h2><p class="muted">One CSV brings your daily sales and stock together to build your 7- and 14-day demand estimates.</p></div>
<div class="upload-layout"><form id="pharmacy-upload"><fieldset><label class="csv-picker"><span class="upload-icon" aria-hidden="true">↑</span><strong id="upload-filename">Choose a CSV file</strong><span id="upload-filedetail">or drop it here · CSV only · up to 5 MB</span><input type="file" name="csv" accept=".csv,text/csv" aria-label="Choose sales and inventory CSV" aria-describedby="upload-status" required></label><div class="upload-actions"><button class="btn" type="submit" disabled>Generate forecast <span aria-hidden="true">→</span></button><a href="/public/sales-template.csv" download>Download CSV template ↓</a></div></fieldset><p id="upload-status" role="status" aria-live="polite">Choose a file to get started.</p><p class="upload-privacy">Your CSV and fitted model are discarded after processing. Your plan includes a saved 30-day stock and sales summary.</p></form>
<aside class="csv-guide"><h3>A quick file check</h3><p>One row per medicine, per day. Aim for 60+ days of history; at least 20 dates must match available PULSE signals.</p><details><summary>View column guide</summary><h4>Required</h4><ul><li><code>date</code>: YYYY-MM-DD</li><li><code>drug_name</code>: medicine name</li><li><code>unit</code>: e.g. tablets; keep consistent</li><li><code>starting_stock</code>: stock before sales</li><li><code>quantity_sold</code>: daily units sold</li></ul><h4>Optional</h4><p><code>ending_stock</code>, <code>current_stock</code>, <code>expected_delivery</code></p><p>Use nonnegative quantities. Do not repeat a medicine/date pair or report sales above starting stock. The two template rows illustrate the format; replace them with your history.</p></details></aside></div></section>
    ${hasPlan ? `<section class="workspace-section" id="inventory-risk" aria-labelledby="risk-title"><div class="workspace-section-head"><div><p class="eyebrow">Inventory focus</p><h2 id="risk-title">At-risk inventory</h2></div>${risk.length ? `<label class="risk-picker">Choose a medicine<select id="risk-medicine">${risk.map((r,i) => `<option value="${i}">${escapeHTML(r.drug_name)}</option>`).join('')}</select></label>` : ''}</div><p class="muted">${risk.length ? '7-day estimated demand exceeds on-hand stock for these medicines. Incoming deliveries are included in purchase estimates.' : 'No medicines have 7-day estimated demand above their supplied on-hand stock.'}</p>${risk.length ? `<article class="inventory-card" id="risk-chart">${inventoryChart(risk[0])}</article>` : ''}</section>
    <section class="workspace-section" aria-labelledby="forecast-title"><div class="workspace-section-head"><div><p class="eyebrow">Next order</p><h2 id="forecast-title">Your medication list</h2></div><label class="medicine-search">Find a medicine<input type="search" id="medicine-search" placeholder="Search medicines"></label></div><p class="muted">Demand above on-hand stock comes first, then the largest 7-day purchase estimate (14-day estimate breaks ties). Quantities are in each medicine’s listed unit.</p><div class="table-scroll purchase-table-wrap"><table class="table purchase-table"><thead><tr><th scope="col">Medicine / unit</th><th scope="col">On hand</th><th scope="col">Incoming</th><th scope="col">Demand<small>7 days</small></th><th scope="col">Demand<small>14 days</small></th><th scope="col">Purchase<small>7 days</small></th><th scope="col">Purchase<small>14 days</small></th></tr></thead><tbody>${rows}</tbody></table></div><p class="muted" id="medicine-empty" hidden>No medicines match your search.</p><p class="workspace-research-note">Experimental research estimates, not validated purchasing guidance. Purchase estimates include three safety-stock days and subtract on-hand stock and reported deliveries. Quantities across different units do not measure comparable clinical urgency. Check supplier timing and review the plan before ordering.</p></section>` : ''}`;
  $('#pharmacy-logout').onclick=signOut;
  const reupload = $('#reupload-data'), upload = $('#workspace-upload');
  if (reupload) reupload.onclick = () => {
    upload.hidden = !upload.hidden;
    reupload.setAttribute('aria-expanded', String(!upload.hidden));
    reupload.textContent = upload.hidden ? 'Reupload data' : 'Cancel reupload';
    if (!upload.hidden) { upload.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',block:'start'}); upload.querySelector('input').focus({preventScroll:true}); }
  };
  const selector = $('#risk-medicine');
  if (selector) selector.onchange = () => { $('#risk-chart').innerHTML = inventoryChart(risk[Number(selector.value)]); };
  const table = $('.purchase-table');
  if (table) {
    table.onclick = event => {
      const button = event.target.closest('[data-risk-index]');
      if (!button) return;
      selector.value = button.dataset.riskIndex; selector.onchange();
      $('#risk-chart').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',block:'start'});
    };
    $('#medicine-search').oninput = event => {
      const query = event.target.value.trim().toLowerCase(), rows = [...table.tBodies[0].rows];
      rows.forEach(row => { row.hidden = !row.cells[0].textContent.toLowerCase().includes(query); });
      $('#medicine-empty').hidden = rows.some(row => !row.hidden);
    };
  }
  loadWorkspaceNews();
  const form = $('#pharmacy-upload'), input = form.csv, button = form.querySelector('button'), status = $('#upload-status');
  input.onchange = () => {
    const file = input.files[0], error = uploadError(file);
    $('#upload-filename').textContent = file?.name || 'Choose a CSV file';
    $('#upload-filedetail').textContent = file ? `${(file.size / 1024).toFixed(1)} KB · Click to choose another file` : 'or drop it here · CSV only · up to 5 MB';
    input.closest('label').classList.toggle('is-ready', !!file && !error);
    button.disabled = !!error;
    status.dataset.state = file && error ? 'error' : '';
    status.textContent = error || 'Ready to generate your forecast.';
  };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const file = input.files[0], error = uploadError(file), request = workspaceRequest;
    if (error) { status.textContent = error; return; }
    form.querySelector('fieldset').disabled = true;
    form.setAttribute('aria-busy', 'true');
    button.textContent = 'Generating forecast…';
    status.dataset.state = '';
    status.textContent = 'Processing your sales history. Keep this page open.';
    try {
      const response = await fetch('/api/v1/plan', {method:'POST', headers:{'Content-Type':'text/csv'}, body:await file.text()});
      const data = await response.json();
      if (!response.ok) throw Error(data.detail || 'Upload failed. Please try again.');
      if (request === workspaceRequest) renderPharmacy(el, data, 'Forecast regenerated; source CSV discarded.');
    } catch (error) { status.dataset.state = 'error'; status.textContent = error.message; }
    finally { form.querySelector('fieldset').disabled = false; form.removeAttribute('aria-busy'); button.textContent = 'Generate forecast →'; }
  };
}
const tour = document.querySelector('#quick-tour');
const tourStages = [...tour.querySelectorAll('[data-tour-stage]')];
const tourSteps = [...tour.querySelectorAll('[data-tour-step]')];
const tourToggle = tour.querySelector('.tour-toggle');
const tourCursor = tour.querySelector('#tour-cursor');
const tourViewport = tour.querySelector('.tour-viewport');
const stockPath = tour.querySelector('#tour-stock-path');
const stockPoint = tour.querySelector('#tour-stock-point');
const stockLength = stockPath.getTotalLength();
const bars = [...tour.querySelectorAll('.tour-bars i')];
const tourStops = [0, 2200, 5600, 9700, 14500];
const tourEmail = 'demo_pharmacy';
const stockDays = [132,116,100,85,69,57,44,30,15,0];
const stockCoords = [[28,38],[63,54],[98,69],[133,83],[168,99],[203,111],[238,123],[273,136],[308,149],[343,164]];
const cursorFrames = [
  [[0,[.45,.16]],[1500,'#tour-open-workspace'],[2200,'#tour-open-workspace']],
  [[0,[.46,.14]],[500,'#tour-email'],[1800,'#tour-email'],[1950,'#tour-password'],[2750,'#tour-password'],[3100,'#tour-submit'],[3400,'#tour-submit']],
  [[0,[.46,.12]],[650,'#tour-sales-button'],[1100,'#tour-sales-button'],[1600,'#tour-inventory-button'],[2050,'#tour-inventory-button'],[2650,'#tour-train-button'],[4100,'#tour-train-button']],
  [[0,[.45,.14]],[1400,'#tour-stock-svg']]
];
let tourTime = 0, tourIndex = -1, tourPlaying = !matchMedia('(prefers-reduced-motion: reduce)').matches, tourVisible = false, lastTourFrame = 0, tourFrame;
const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));
function cursorPosition(target) {
  const viewport = tourViewport.getBoundingClientRect();
  if (Array.isArray(target)) return { x: viewport.width * target[0], y: viewport.height * target[1] };
  const rect = tour.querySelector(target).getBoundingClientRect();
  return { x: rect.left - viewport.left + rect.width * (target === '#tour-stock-svg' ? .15 : .3), y: rect.top - viewport.top + rect.height * .5 };
}
function setCursor(x, y) {
  tourCursor.style.setProperty('--cursor-x', x - 2 + 'px');
  tourCursor.style.setProperty('--cursor-y', y - 2 + 'px');
}
function renderTour() {
  const index = tourStops.findIndex((stop, i) => i < 4 && tourTime >= stop && tourTime < tourStops[i + 1]);
  if (index !== tourIndex) {
    tourIndex = index;
    tourStages.forEach((stage, i) => stage.setAttribute('aria-hidden', String(i !== index)));
    tourSteps.forEach((step, i) => step.setAttribute('aria-pressed', String(i === index)));
    tour.querySelector('#tour-url').textContent = ['pulse / home','pulse / sign up','pulse / workspace','pulse / results'][index];
    tour.querySelector('#tour-nav-action').textContent = index ? 'Account' : 'Sign up';
  }
  const local = tourTime - tourStops[index];
  tourSteps[index].style.setProperty('--step-progress', local / (tourStops[index + 1] - tourStops[index]));
  const frames = cursorFrames[index];
  const next = frames.findIndex(frame => frame[0] >= local);
  const start = next < 0 ? frames.at(-1) : frames[Math.max(0, next - 1)];
  const end = next < 0 ? start : frames[next];
  const from = cursorPosition(start[1]), to = cursorPosition(end[1]);
  const progress = start === end ? 1 : clamp((local - start[0]) / (end[0] - start[0]));
  const ease = progress * progress * (3 - 2 * progress);
  let x = from.x + (to.x - from.x) * ease, y = from.y + (to.y - from.y) * ease;
  if (index === 3 && tourTime >= 11600) {
    const day = clamp((tourTime - 11600) / 1800, 0, 1) * 9;
    const whole = Math.floor(day), fraction = day - whole;
    const left = stockCoords[whole], right = stockCoords[Math.min(9, whole + 1)];
    const chartX = left[0] + (right[0] - left[0]) * fraction;
    const chartY = left[1] + (right[1] - left[1]) * fraction;
    const screen = new DOMPoint(chartX, chartY).matrixTransform(tour.querySelector('#tour-stock-svg').getScreenCTM());
    const viewport = tourViewport.getBoundingClientRect();
    x = screen.x - viewport.left; y = screen.y - viewport.top;
    stockPoint.setAttribute('cx', chartX); stockPoint.setAttribute('cy', chartY);
    tour.querySelector('#tour-stock-tooltip').textContent = `Day ${Math.round(day)} · ${stockDays[Math.round(day)]} units`;
  } else if (index === 3) {
    stockPoint.setAttribute('cx', '28'); stockPoint.setAttribute('cy', '38');
    tour.querySelector('#tour-stock-tooltip').textContent = 'On hand · 132 units';
  }
  setCursor(x, y);
  tourCursor.classList.toggle('is-clicking', [1900,2700,4200,5350,6400,7350,8400].some(t => Math.abs(tourTime - t) < 110));
  tour.querySelector('#tour-email').textContent = index === 1 ? tourEmail.slice(0, clamp(Math.floor((tourTime - 2900) / 60), 0, tourEmail.length)) : '';
  tour.querySelector('#tour-password').textContent = index === 1 ? '•'.repeat(clamp(Math.floor((tourTime - 4320) / 48), 0, 12)) : '';
  tour.querySelector('#tour-sales-name').textContent = index === 2 && tourTime >= 6600 ? 'sample_sales.csv ✓' : 'No file selected';
  tour.querySelector('#tour-inventory-name').textContent = index === 2 && tourTime >= 7550 ? 'Stock columns included ✓' : 'No file selected';
  tour.querySelector('#tour-train-status').textContent = index !== 2 || tourTime < 8400 ? 'Ready when sales history is selected.' : tourTime < 8800 ? 'Preparing example results...' : tourTime < 9200 ? 'Loading example plan...' : 'Building your plan…';
  tour.querySelector('#tour-progress').style.width = index === 2 ? clamp((tourTime - 8500) / 1150) * 100 + '%' : '0%';
  const reveal = index === 3 ? clamp((tourTime - 9900) / 1400) : 0;
  stockPath.style.strokeDasharray = stockLength;
  stockPath.style.strokeDashoffset = stockLength * (1 - reveal);
  tour.querySelector('.tour-stock-fill').style.opacity = reveal;
  bars.forEach((bar, i) => bar.style.setProperty('--bar-progress', clamp((tourTime - 10200 - i * 55) / 500)));
  tour.querySelector('#tour-demand-number').textContent = String(Math.round(214 * reveal));
  tour.querySelector('#tour-order-number').textContent = String(Math.round(26 * clamp((tourTime - 11000) / 850)));
}
function tourTick(now) {
  if (lastTourFrame) tourTime = (tourTime + Math.min(now - lastTourFrame, 80)) % 14500;
  lastTourFrame = now;
  renderTour();
  tourFrame = requestAnimationFrame(tourTick);
}
function syncTour() {
  const running = tourPlaying && tourVisible && !document.hidden && document.querySelector('#home').classList.contains('active');
  cancelAnimationFrame(tourFrame);
  lastTourFrame = 0;
  tour.classList.toggle('is-playing', running);
  tourToggle.textContent = tourPlaying ? 'Pause' : 'Play';
  tourToggle.setAttribute('aria-label', tourPlaying ? 'Pause walkthrough' : 'Play walkthrough');
  if (running) tourFrame = requestAnimationFrame(tourTick);
}
tourSteps.forEach((step, index) => step.addEventListener('click', () => {
  tourPlaying = false;
  tourTime = Math.min(tourStops[index + 1] - 100, tourStops[index] + [1950, 3200, 3800, 3900][index]);
  renderTour(); syncTour();
}));
tourToggle.addEventListener('click', () => { tourPlaying = !tourPlaying; syncTour(); });
new IntersectionObserver(([entry]) => { tourVisible = entry.isIntersecting; syncTour(); }, { threshold: .15 }).observe(tour);
document.addEventListener('visibilitychange', syncTour);
renderTour();
syncTour();
const graphCards = [...document.querySelectorAll('#home .editorial-grid .editorial-card')];
if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
  document.documentElement.classList.add('has-graph-motion');
  graphCards.forEach(card => {
    card.querySelectorAll('.card-art circle, .card-art g rect').forEach((shape, index) => shape.style.setProperty('--graph-index', index));
    const svg = card.querySelector('.card-art svg');
    const line = svg.querySelector('path[fill="none"]:not(.art-rule)');
    if (line) {
      const marker = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      const motion = document.createElementNS('http://www.w3.org/2000/svg', 'animateMotion');
      marker.setAttribute('class', 'graph-travel');
      marker.setAttribute('r', '5');
      motion.setAttribute('path', line.getAttribute('d'));
      motion.setAttribute('dur', '7s');
      motion.setAttribute('repeatCount', 'indefinite');
      motion.setAttribute('calcMode', 'spline');
      motion.setAttribute('keyTimes', '0;.5;1');
      motion.setAttribute('keyPoints', '0;1;0');
      motion.setAttribute('keySplines', '.4 0 .6 1;.4 0 .6 1');
      marker.append(motion);
      svg.append(marker);
    }
    svg.pauseAnimations();
  });
  const visibleGraphs = new Set();
  function syncGraphMotion() {
    graphCards.forEach(card => {
      const playing = !document.hidden && visibleGraphs.has(card);
      card.classList.toggle('is-in-view', playing);
      if (playing) card.classList.add('is-animated');
      card.querySelector('.card-art svg')[playing ? 'unpauseAnimations' : 'pauseAnimations']();
    });
  }
  const graphObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.intersectionRatio >= .25) visibleGraphs.add(entry.target);
      else visibleGraphs.delete(entry.target);
    });
    syncGraphMotion();
  }, { threshold: .25 });
  graphCards.forEach(card => graphObserver.observe(card));
  document.addEventListener('visibilitychange', syncGraphMotion);
}
document.querySelectorAll('.faq-question').forEach(button => button.addEventListener('click', () => {
  const item = button.closest('.faq-item'), open = item.classList.toggle('is-open');
  button.setAttribute('aria-expanded', String(open));
  item.querySelector('.faq-panel').setAttribute('aria-hidden', String(!open));
}));
show(location.hash.slice(1) || 'home');
if (!['workspace','access'].includes(currentPage)) { const request = workspaceRequest; api('/plan').then(() => { if (request === workspaceRequest) setSignedIn(true); }).catch(() => { if (request === workspaceRequest) setSignedIn(false); }); }

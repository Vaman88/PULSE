const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const prettyDisease = key => key.replace(/_([12])w$/, '').replaceAll('_', ' ');
let docsLoaded = false;
async function api(path, options = {}) {
  const response = await fetch('/api/v1' + path, {...options, headers:{Accept:'application/json', ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw Error(Array.isArray(data.detail) ? data.detail.map(item => `${item.loc.at(-1)}: ${item.msg}`).join('; ') : data.detail || 'The API request failed');
  return data;
}
let currentPage = 'home', backStack = [], signedIn = false, workspaceRequest = 0;
const pages = {home:'Home', workspace:'Workspace', inventory:'Inventory', signals:'Signals', api:'API', access:'Sign in', privacy:'Privacy', terms:'Use & limitations'};
function updateNavigation() {
  const active = $('.nav button.active'), indicator = $('.nav-indicator');
  indicator.style.opacity = active ? '1' : '0';
  if (active) { indicator.style.width = active.offsetWidth + 'px'; indicator.style.transform = `translateX(${active.offsetLeft}px)`; }
  const tabs = $('#workspace-nav');
  tabs.hidden = !signedIn || !['workspace','inventory'].includes(currentPage);
  tabs.querySelectorAll('[data-screen]').forEach(node => { const active = node.dataset.screen === currentPage; node.classList.toggle('active', active); if (active) node.setAttribute('aria-current','page'); else node.removeAttribute('aria-current'); });
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
    node.classList.toggle('active', node.dataset.screen === (page === 'inventory' ? 'workspace' : page));
    if (node.dataset.screen === (page === 'inventory' ? 'workspace' : page)) node.setAttribute('aria-current','page'); else node.removeAttribute('aria-current');
  });
  if (page === 'signals') loadSignals();
  if (page === 'api' && !docsLoaded) loadDocs();
  if (page === 'workspace' || page === 'access') loadPharmacy();
  if (page === 'inventory') loadInventory();
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
    setSignedIn(false); inventoryLoad++; inventoryItems = []; $('#inventory-content').replaceChildren(); $('#pharmacy-content').replaceChildren(); show('access');
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
const wsIcons = {
  upload:'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 14V4M6 8l4-4 4 4M4 16h12"/></svg>',
  download:'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v10M6 10l4 4 4-4M4 16h12"/></svg>',
  signals:'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 16h14M5 13l3-4 3 2 4-6"/></svg>',
  out:'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12 5V3.5H4v13h8V15M9 10h8M14 7l3 3-3 3"/></svg>',
  check:'<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7"/><path d="m7 10 2 2 4-4"/></svg>',
  news:'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 4h12v12H4zM7 8h6M7 11h6M7 14h3"/></svg>',
  chart:'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 16h14M5 13l3-4 3 2 4-6"/></svg>',
  search:'<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m13 13 4 4"/></svg>'
};
function stockCover(item) { return item.demand_7d > 0 ? Math.max(0, Math.min(100, Math.round(item.current_stock / item.demand_7d * 100))) : 100; }
function animateCounts(root) {
  if (typeof requestAnimationFrame !== 'function' || typeof matchMedia !== 'function' || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  root.querySelectorAll('[data-count]').forEach(node => {
    const target = Number(node.dataset.count);
    if (!Number.isFinite(target)) return;
    let started = 0;
    const step = now => {
      if (!started) started = now;
      const progress = Math.min(1, (now - started) / 900);
      node.textContent = String(Math.round(target * (1 - Math.pow(1 - progress, 3))));
      if (progress < 1) requestAnimationFrame(step);
    };
    node.textContent = '0';
    requestAnimationFrame(step);
  });
}
function planReadout(rec, risk) {
  const head = `<div class="ws-panel-head">${wsIcons.check}<h2 id="readout-title">Plan readout</h2><span class="ws-tag"><i></i>Plan ready</span></div>`;
  if (!risk.length) return `${head}<div class="ws-readout-main is-calm"><div><small class="ws-label">Needs attention</small><strong>${rec.length ? 'No medicines' : 'No medicines in this plan'}</strong><p>${rec.length ? `7-day estimated demand is within supplied on-hand stock for all ${rec.length} medicines.` : 'Reupload data to build estimates for your medicines.'}</p></div></div>`;
  const top = risk[0], unit = escapeHTML(top.unit), others = risk.slice(1, 5);
  return `${head}<div class="ws-readout-main"><div><small class="ws-label">Needs attention</small><strong>${escapeHTML(top.drug_name)}</strong><span class="ws-flags"><em class="is-warn">Demand exceeds on-hand</em><em>${top.expected_delivery ?? 0} ${unit} incoming</em></span><button class="ws-open" type="button" data-risk-index="0">Open stock &amp; demand <span aria-hidden="true">→</span></button></div>
    <div class="ws-big"><small class="ws-label">Purchase · 7 days</small>${top.buy_7d == null ? '<b>Conditional</b>' : `<b data-count="${top.buy_7d}">${top.buy_7d}</b>`}<span>${unit} to review</span></div></div>
    ${others.length ? `<div class="ws-others"><small class="ws-label">Also needs attention · on-hand stock covers</small>${others.map((r, i) => `<button type="button" data-risk-index="${i + 1}"><span>${escapeHTML(r.drug_name)}</span><b>${stockCover(r)}%</b><i aria-hidden="true"><em style="--w:${stockCover(r) / 100}"></em></i><small class="ws-sr"> of its 7-day estimated demand</small></button>`).join('')}${risk.length > 5 ? `<p class="ws-more">+ ${risk.length - 5} more in your medication list</p>` : ''}</div>` : ''}
    <div class="ws-next"><small class="ws-label">What to do next</small>Open each medicine’s outlook, then review the order list before ordering.</div>`;
}
function orderList(risk) {
  return `<small class="ws-label">Purchase estimates · medicines that need attention</small><ol class="ws-order-list">${risk.map((r, i) => `<li><i>${i + 1}</i><span>${escapeHTML(r.drug_name)}<small>${escapeHTML(r.unit)}</small></span><b>${r.buy_7d ?? 'Conditional'}<small>7 days</small></b><b class="is-quiet">${r.buy_14d ?? 'Conditional'}<small>14 days</small></b></li>`).join('')}</ol>
    <div class="ws-note"><b>Before ordering</b>Estimates subtract on-hand stock and reported deliveries and include three safety-stock days. Check supplier timing and confirm quantities with a qualified pharmacist.</div>`;
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
  const news = `<section class="ws-panel workspace-news-section ws-rise" style="--d:3" aria-labelledby="workspace-news-title"><div class="ws-panel-head">${wsIcons.news}<h2 id="workspace-news-title">Relevant news</h2><a href="#signals" data-go="signals" class="workspace-text-link">All public signals ↗</a></div><p class="ws-panel-sub">Recent disease-related reporting. These articles provide context, not proof of demand for a particular medicine.</p><div id="workspace-news" class="ws-news" role="status"><p class="muted">Loading recent reporting…</p></div></section>`;
  const upload = `<section class="ws-panel workspace-upload ws-rise" style="--d:2" id="workspace-upload" aria-labelledby="upload-title" ${hasPlan ? 'hidden' : ''}>
<div class="upload-intro"><p class="eyebrow">Sales &amp; inventory</p><h2 id="upload-title">Start with your sales history.</h2><p class="muted">One CSV brings your daily sales and stock together to build your 7- and 14-day demand estimates.</p></div>
<div class="upload-layout"><form id="pharmacy-upload"><fieldset><label class="csv-picker"><span class="upload-icon" aria-hidden="true">${wsIcons.upload}</span><strong id="upload-filename">Choose a CSV file</strong><span id="upload-filedetail">or drop it here · CSV only · up to 5 MB</span><input type="file" name="csv" accept=".csv,text/csv" aria-label="Choose sales and inventory CSV" aria-describedby="upload-status" required></label><div class="upload-actions"><button class="btn" type="submit" disabled>Generate forecast <span aria-hidden="true">→</span></button><a href="/public/sales-template.csv" download>Download CSV template ↓</a></div></fieldset><div class="ws-progress" aria-hidden="true"><i></i></div><p id="upload-status" role="status" aria-live="polite">Choose a file to get started.</p><p class="upload-privacy">Your CSV and fitted model are discarded after processing. Your plan includes a saved 30-day stock and sales summary.</p></form>
<aside class="csv-guide"><h3>A quick file check</h3><p>One row per medicine, per day. Aim for 60+ days of history; at least 20 dates must match available PULSE signals.</p><ul class="ws-columns"><li><code>date</code><span>YYYY-MM-DD</span></li><li><code>drug_name</code><span>medicine name</span></li><li><code>unit</code><span>e.g. tablets; keep consistent</span></li><li><code>starting_stock</code><span>stock before sales</span></li><li><code>quantity_sold</code><span>daily units sold</span></li><li class="is-optional"><code>ending_stock</code><span>optional</span></li><li class="is-optional"><code>current_stock</code><span>optional</span></li><li class="is-optional"><code>expected_delivery</code><span>optional</span></li></ul><p class="ws-rules">Use nonnegative quantities. Do not repeat a medicine/date pair or report sales above starting stock. The two template rows illustrate the format; replace them with your history.</p></aside></div></section>`;
  el.innerHTML = `<div class="ws">
    <div class="ws-chips ws-rise">${hasPlan ? `<button id="reupload-data" class="ws-chip" type="button" aria-expanded="false" aria-controls="workspace-upload">${wsIcons.upload}<span>Reupload data</span></button>` : ''}<a class="ws-chip" href="/public/sales-template.csv" download>${wsIcons.download}<span>CSV template</span></a><a class="ws-chip" href="#signals" data-go="signals">${wsIcons.signals}<span>Public signals</span></a><button id="pharmacy-logout" class="ws-chip ws-chip-end" type="button">${wsIcons.out}<span>Sign out</span></button></div>
    <div class="ws-window ws-rise" style="--d:1">
      <div class="ws-head"><div><p class="eyebrow">Your pharmacy workspace</p><h1>${hasPlan ? 'Demand &amp; inventory' : 'Build your demand plan'}</h1><p class="muted">${hasPlan ? 'Review the medicines that need attention before your next order.' : 'Bring your sales and stock together to build your first demand plan.'}</p></div><div class="ws-pills"><span class="ws-pill">7 / 14-day outlook</span>${plan.illustrative ? '<span class="ws-pill is-demo">Demo · synthetic data</span>' : ''}</div></div>
      ${plan.illustrative ? '<p class="status" role="status"><strong>Demo workspace:</strong> All inventory, sales history, and demand estimates shown here are synthetic examples for demonstration.</p>' : ''}
      ${message ? `<p class="status" role="status">${escapeHTML(message)}</p>` : ''}
      ${hasPlan ? `<div class="stats"><div class="stat">Medications<strong data-count="${rec.length}">${rec.length}</strong></div><div class="stat">Need attention<strong data-count="${risk.length}">${risk.length}</strong></div><div class="stat">Demand outlook<strong>7 / 14 days</strong></div><div class="stat">Plan updated<strong>${escapeHTML(plan.generated_at?.slice(0,10) || 'Not recorded')}</strong></div></div>
      <div class="ws-cols"><section class="ws-panel ws-readout ws-rise" style="--d:2" id="plan-readout" aria-labelledby="readout-title">${planReadout(rec, risk)}</section>${news}</div>
      ${upload}` : `${upload}${news}`}
    </div>
    ${hasPlan ? `<section class="ws-section ws-rise" style="--d:4" id="inventory-risk" aria-labelledby="risk-title"><div class="workspace-section-head"><div><p class="eyebrow">Inventory focus</p><h2 id="risk-title">At-risk inventory</h2></div>${risk.length ? `<label class="risk-picker">Choose a medicine<select id="risk-medicine">${risk.map((r,i) => `<option value="${i}">${escapeHTML(r.drug_name)}</option>`).join('')}</select></label>` : ''}</div><p class="muted">${risk.length ? '7-day estimated demand exceeds on-hand stock for these medicines. Incoming deliveries are included in purchase estimates.' : 'No medicines have 7-day estimated demand above their supplied on-hand stock.'}</p>${risk.length ? `<div class="ws-panel ws-detail"><div class="ws-tabs" role="tablist" aria-label="Medicine detail"><button type="button" role="tab" id="risk-tab-chart" aria-selected="true" aria-controls="risk-chart" tabindex="0">${wsIcons.chart}Stock &amp; demand</button><button type="button" role="tab" id="risk-tab-order" aria-selected="false" aria-controls="risk-order" tabindex="-1">Order list</button></div><article class="inventory-card" id="risk-chart" role="tabpanel" aria-labelledby="risk-tab-chart">${inventoryChart(risk[0])}</article><div class="ws-order" id="risk-order" role="tabpanel" aria-labelledby="risk-tab-order" hidden>${orderList(risk)}</div></div>` : ''}</section>
    <section class="ws-section ws-rise" style="--d:5" aria-labelledby="forecast-title"><div class="workspace-section-head"><div><p class="eyebrow">Next order</p><h2 id="forecast-title">Your medication list</h2></div><label class="medicine-search">Find a medicine<span class="ws-search">${wsIcons.search}<input type="search" id="medicine-search" placeholder="Search medicines"></span></label></div><p class="muted">Demand above on-hand stock comes first, then the largest 7-day purchase estimate (14-day estimate breaks ties). Quantities are in each medicine’s listed unit.</p><div class="table-scroll purchase-table-wrap"><table class="table purchase-table"><thead><tr><th scope="col">Medicine / unit</th><th scope="col">On hand</th><th scope="col">Incoming</th><th scope="col">Demand<small>7 days</small></th><th scope="col">Demand<small>14 days</small></th><th scope="col">Purchase<small>7 days</small></th><th scope="col">Purchase<small>14 days</small></th></tr></thead><tbody>${rows}</tbody></table></div><p class="muted" id="medicine-empty" hidden>No medicines match your search.</p><p class="workspace-research-note">Experimental research estimates, not validated purchasing guidance. Purchase estimates include three safety-stock days and subtract on-hand stock and reported deliveries. Quantities across different units do not measure comparable clinical urgency. Check supplier timing and review the plan before ordering.</p></section>` : ''}
  </div>`;
  $('#pharmacy-logout').onclick=signOut;
  const reupload = $('#reupload-data'), uploadSection = $('#workspace-upload');
  if (reupload) reupload.onclick = () => {
    uploadSection.hidden = !uploadSection.hidden;
    reupload.setAttribute('aria-expanded', String(!uploadSection.hidden));
    reupload.querySelector('span').textContent = uploadSection.hidden ? 'Reupload data' : 'Cancel reupload';
    if (!uploadSection.hidden) { uploadSection.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',block:'start'}); uploadSection.querySelector('input').focus({preventScroll:true}); }
  };
  const selector = $('#risk-medicine'), tabs = [$('#risk-tab-chart'), $('#risk-tab-order')].filter(Boolean);
  const selectTab = tab => tabs.forEach(item => {
    const active = item === tab;
    item.setAttribute('aria-selected', String(active));
    item.tabIndex = active ? 0 : -1;
    $('#' + item.getAttribute('aria-controls')).hidden = !active;
  });
  tabs.forEach((tab, index) => {
    tab.onclick = () => selectTab(tab);
    tab.onkeydown = event => {
      const next = {ArrowRight:index + 1, ArrowLeft:index - 1, Home:0, End:tabs.length - 1}[event.key];
      if (next === undefined) return;
      event.preventDefault();
      const target = tabs[(next + tabs.length) % tabs.length];
      selectTab(target); target.focus();
    };
  });
  if (selector) selector.onchange = () => { $('#risk-chart').innerHTML = inventoryChart(risk[Number(selector.value)]); if (tabs.length) selectTab(tabs[0]); };
  const openRisk = index => {
    selector.value = index; selector.onchange();
    $('#inventory-risk').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',block:'start'});
  };
  const readout = $('#plan-readout');
  if (readout && selector) readout.onclick = event => { const button = event.target.closest('[data-risk-index]'); if (button) openRisk(button.dataset.riskIndex); };
  const table = $('.purchase-table');
  if (table) {
    table.onclick = event => {
      const button = event.target.closest('[data-risk-index]');
      if (button) openRisk(button.dataset.riskIndex);
    };
    $('#medicine-search').oninput = event => {
      const query = event.target.value.trim().toLowerCase(), rows = [...table.tBodies[0].rows];
      rows.forEach(row => { row.hidden = !row.cells[0].textContent.toLowerCase().includes(query); });
      $('#medicine-empty').hidden = rows.some(row => !row.hidden);
    };
  }
  loadWorkspaceNews();
  animateCounts(el);
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
const tourStage = tour.querySelector('.tour-stage');
const tourWindow = tour.querySelector('.tour-window');
const tourCursor = tour.querySelector('#tour-cursor');
const tq = selector => tour.querySelector(selector);
const tqa = selector => [...tour.querySelectorAll(selector)];
const reducedTour = matchMedia('(prefers-reduced-motion: reduce)').matches;
// One 22-second loop on a single clock. Times below are milliseconds from the start of the loop.
const tourLength = 22000;
const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));
const easeOut = t => 1 - Math.pow(1 - clamp(t), 3);
const span = (time, start, duration) => easeOut((time - start) / duration);
const between = (time, start, end) => time >= start && time < end;
// Synthetic rows; each closing stock matches the demo workspace (e.g. 482 - 141 = 341 on hand).
const tourSheet = [
  ['2026-11-04','Metformin ER 500 mg',482,141],['2026-11-04','Metformin 500 mg',188,98],['2026-11-04','Sertraline 50 mg',495,95],
  ['2026-11-04','Atorvastatin 20 mg',134,134],['2026-11-04','Cetirizine 10 mg',125,87],['2026-11-04','Lisinopril 10 mg',672,60],
  ['2026-11-03','Metformin ER 500 mg',620,138],['2026-11-03','Metformin 500 mg',287,99],['2026-11-03','Sertraline 50 mg',590,95]
];
const tourSignals = [['Influenza',7],['Measles',6],['Pertussis',5],['Avian influenza',4],['Dengue',3],['Mpox',2]];
const tourStock = [560,470,1230,1100,975,850,720,590,470,1230,1100,975,850,720,590,470,1230,1100,975,850,720,590,470,1230,1100,975,850,720,590,470,341];
const tourSales = [142,158,163,150,131,128,139,154,161,148,129,133,145,156,160,141,126,138,152,163,147,130,135,149,158,164,151,137,144,150,155];
(function buildTour() {
  tq('#tour-sheet-body').innerHTML = tourSheet.map(([date, name, start, sold]) => `<div class="tour-sheet-row"><span>${date}</span><span>${name}</span><span>tablet</span><span>${start}</span><span>${sold}</span></div>`).join('');
  tq('#ts-rows').innerHTML = tourSignals.map(([name, score], i) => `<div class="tour-signal" id="ts-row-${i}" data-score="${score}"><span>${name}</span><i><em></em></i><b>0</b></div>`).join('');
  const x = i => 34 + i * 340 / 30, stockY = v => 88 - v / 1300 * 66, salesY = v => 184 - v / 170 * 62;
  const demandX = i => 386 + i * 120 / 13, demandY = salesY(148);
  tq('#tour-chart').innerHTML = `<rect class="tour-band" x="380" y="4" width="132" height="182" rx="7"/><line class="tour-divider" x1="380" x2="380" y1="4" y2="186"/>
    <path class="tour-grid" d="M34 22H374M34 88H374M34 122H374M34 184H374"/>
    <text x="34" y="14">Stock</text><text x="34" y="114">Daily sales</text><text class="tour-band-label" x="390" y="20">14-day outlook</text>
    ${tourSales.map((v, i) => `<rect class="tour-sale" x="${(x(i) - 3.5).toFixed(1)}" y="${salesY(v).toFixed(1)}" width="7" height="${(184 - salesY(v)).toFixed(1)}" rx="1.5"/>`).join('')}
    <path class="tour-stock-line" id="tour-stock-line" d="${tourStock.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${stockY(v).toFixed(1)}`).join(' ')}"/>
    <circle class="tour-pop" id="tour-stock-dot" r="4" cx="${x(30)}" cy="${stockY(341)}"/>
    <path class="tour-demand-line" id="tour-demand-line" d="M${demandX(0)} ${demandY.toFixed(1)}H${demandX(13)}"/>
    ${Array.from({length:14}, (_, i) => `<circle class="tour-demand-dot" cx="${demandX(i).toFixed(1)}" cy="${demandY.toFixed(1)}" r="2.6"/>`).join('')}
    <text class="tour-band-value" x="390" y="${(demandY - 12).toFixed(1)}">148 / day</text>
    <text x="34" y="204">Oct 5</text><text x="378" y="204" text-anchor="end">Nov 4</text><text x="512" y="204" text-anchor="end">Nov 18</text>`;
})();
const tourStockLine = tq('#tour-stock-line'), tourDemandLine = tq('#tour-demand-line');
const tourStockLength = tourStockLine.getTotalLength(), tourDemandLength = tourDemandLine.getTotalLength();
const tourSheetRows = tqa('.tour-sheet-body .tour-sheet-row'), tourChecks = tqa('.tour-checks li');
const tourOthers = tqa('.tour-others [data-bar]'), tourSales$ = tqa('.tour-sale'), tourDemandDots = tqa('.tour-demand-dot');
const tourOrderRows = tqa('.tour-order-list li'), tourSignalRows = tqa('.tour-signal'), tourNews = tqa('.tour-news article');
// Cursor path: [time, target]. Targets are selectors or [x, y] fractions of the app window.
const cursorPath = [
  [0,'#tc-upload'],[650,'#tc-upload'],[1700,[.3,.62]],[2700,[.32,.6]],[3700,[.74,.62]],[5700,[.74,.6]],[6500,'#tour-big-number'],[9000,'#tour-big-number'],
  [9900,'#td-tab-chart'],[10900,'#td-tab-chart'],[11250,'#td-tab-order'],[12800,'#td-tab-order'],[13400,'#ta-signals'],[14700,'#ta-signals'],
  [15600,'#ts-row-0'],[18900,'#ts-row-0'],[19900,'#tc-reset'],[21300,'#tc-reset'],[22000,'#tc-upload']
];
const tourClicks = [700, 11300, 13500, 20000];
let tourTime = reducedTour ? 7600 : 0, tourPlaying = !reducedTour, tourVisible = false, lastTourFrame = 0, tourFrame;
function tourPoint(target) {
  const stage = tourStage.getBoundingClientRect();
  if (Array.isArray(target)) { const w = tourWindow.getBoundingClientRect(); return { x: w.left - stage.left + w.width * target[0], y: w.top - stage.top + w.height * target[1] }; }
  const rect = tq(target).getBoundingClientRect();
  return { x: rect.left - stage.left + rect.width * .5, y: rect.top - stage.top + rect.height * .6 };
}
const setOn = (selector, on) => tq(selector).classList.toggle('is-on', on);
const countUp = (node, value, progress) => { node.textContent = Math.round(value * progress).toLocaleString('en-US'); };
function renderTour() {
  const t = tourTime;
  const signals = between(t, 13600, 20200), working = !signals;
  const reset = t >= 20200;
  // App chrome
  setOn('#tv-work', working); setOn('#tv-signals', signals);
  tq('#tour-url').textContent = signals ? 'pulse / signals' : 'pulse / workspace';
  tq('#ta-work').classList.toggle('is-on', working); tq('#ta-signals').classList.toggle('is-on', signals);
  const tab = tq(signals ? '#ta-signals' : '#ta-work'), line = tq('#tour-tab-line');
  line.style.transform = `translateX(${tab.offsetLeft}px) scaleX(${tab.offsetWidth / 100})`;
  tq('#tc-upload').classList.toggle('is-pressed', between(t, 650, 850));
  tq('#tc-reset').classList.toggle('is-pressed', between(t, 19950, 20150));
  // Left pane: empty → upload → CSV preview
  const live = !reset;
  setOn('#tl-empty', t < 800 || reset); setOn('#tl-upload', between(t, 800, 2200)); setOn('#tl-preview', t >= 2200 && live);
  const uploaded = clamp((t - 900) / 1100);
  tq('#tl-upload-fill').style.transform = `scaleX(${easeOut(uploaded)})`;
  tq('#tl-upload-pct').textContent = Math.round(easeOut(uploaded) * 100) + '%';
  tourSheetRows.forEach((row, i) => row.classList.toggle('is-in', t >= 2250 + i * 55));
  const scanning = between(t, 2500, 5100);
  tq('#tour-scan').classList.toggle('is-on', scanning);
  tq('#tour-scan').style.transform = `translateY(${(((t - 2500) / 850) % 1) * 100}%)`;
  tq('#tour-focus').classList.toggle('is-on', t >= 5250);
  const found = t >= 4000, ready = t >= 5200;
  tq('#tour-tag-text').textContent = ready ? 'Plan ready' : found ? '24 medicines found' : 'Reading';
  tq('#tour-tag').classList.toggle('is-ready', ready);
  tq('#tl-file').textContent = t >= 800 && live ? 'pharmacy_sales.csv · 48 KB' : 'No file yet';
  tq('#tl-state').textContent = !live || t < 800 ? '' : ready ? 'Plan ready' : t >= 2200 ? 'Checking…' : 'Uploading…';
  tq('#tl-state').classList.toggle('is-ready', ready);
  // Right pane steps
  setOn('#tr-empty', t < 800 || reset); setOn('#tr-uploading', between(t, 800, 2400)); setOn('#tr-check', between(t, 2400, 5600));
  setOn('#tr-readout', between(t, 5600, 9200)); setOn('#tr-detail', t >= 9200 && live);
  const checkTimes = [[2550, 3150], [3150, 4000], [4000, 5000]];
  tourChecks.forEach((li, i) => { li.classList.toggle('is-spin', between(t, ...checkTimes[i])); li.classList.toggle('is-done', t >= checkTimes[i][1]); });
  const done = checkTimes.filter(([, end]) => t >= end).length;
  tq('#tour-meter-count').textContent = `${done} / 3`;
  tq('#tour-meter-fill').style.transform = `scaleX(${clamp((t - 2550) / 2450)})`;
  countUp(tq('#tour-big-number'), 735, span(t, 5800, 1100));
  tourOthers.forEach((bar, i) => { bar.classList.toggle('is-in', t >= 6200 + i * 120); bar.querySelector('em').style.transform = `scaleX(${span(t, 6300 + i * 120, 700) * bar.dataset.bar / 100})`; });
  setOn('#tour-next', t >= 7300);
  const orderTab = t >= 11300;
  tq('#td-tab-chart').classList.toggle('is-on', !orderTab); tq('#td-tab-order').classList.toggle('is-on', orderTab);
  setOn('#td-chart', !orderTab); setOn('#td-order', orderTab);
  tourStockLine.style.strokeDasharray = tourStockLength;
  tourStockLine.style.strokeDashoffset = tourStockLength * (1 - span(t, 9450, 1200));
  tq('#tour-stock-dot').classList.toggle('is-in', t >= 10600);
  tourSales$.forEach((bar, i) => { bar.style.transform = `scaleY(${span(t, 9550 + i * 22, 420)})`; });
  tq('.tour-band').style.transform = `scaleX(${span(t, 10300, 500)})`;
  tq('.tour-band-label').classList.toggle('is-in', t >= 10500);
  const demand = span(t, 10550, 600);
  tourDemandLine.style.strokeDasharray = tourDemandLength;
  tourDemandLine.style.strokeDashoffset = tourDemandLength * (1 - demand);
  tourDemandDots.forEach((dot, i) => dot.classList.toggle('is-in', demand > 0 && demand * 13.2 >= i));
  tq('.tour-band-value').classList.toggle('is-in', t >= 11000);
  tourOrderRows.forEach((row, i) => row.classList.toggle('is-in', t >= 11400 + i * 110));
  setOn('.tour-note', t >= 11850);
  // Signals view
  tourSignalRows.forEach((row, i) => {
    const grow = span(t, 13900 + i * 90, 650);
    row.classList.toggle('is-in', t >= 13800 + i * 70);
    row.querySelector('em').style.transform = `scaleX(${grow * row.dataset.score / 10})`;
    countUp(row.querySelector('b'), +row.dataset.score, grow);
  });
  tourSignalRows[0].classList.toggle('is-hover', between(t, 15700, 19000));
  setOn('#ts-pop', between(t, 16000, 19000));
  tourNews.forEach((card, i) => card.classList.toggle('is-in', t >= 14100 + i * 140));
  // Cursor
  const next = cursorPath.findIndex(([time]) => time >= t);
  const from = cursorPath[Math.max(0, next - 1)], to = cursorPath[next < 0 ? cursorPath.length - 1 : next];
  const progress = from === to ? 1 : clamp((t - from[0]) / (to[0] - from[0]));
  const eased = progress < .5 ? 4 * progress ** 3 : 1 - Math.pow(-2 * progress + 2, 3) / 2;
  const a = tourPoint(from[1]), b = tourPoint(to[1]);
  tourCursor.style.setProperty('--cursor-x', a.x + (b.x - a.x) * eased - 3 + 'px');
  tourCursor.style.setProperty('--cursor-y', a.y + (b.y - a.y) * eased - 3 + 'px');
  tourCursor.classList.toggle('is-clicking', tourClicks.some(click => Math.abs(t - click) < 110));
}
function tourTick(now) {
  if (lastTourFrame) tourTime = (tourTime + Math.min(now - lastTourFrame, 80)) % tourLength;
  lastTourFrame = now;
  renderTour();
  tourFrame = requestAnimationFrame(tourTick);
}
function syncTour() {
  const running = tourPlaying && tourVisible && !document.hidden && document.querySelector('#home').classList.contains('active');
  cancelAnimationFrame(tourFrame);
  lastTourFrame = 0;
  tour.classList.toggle('is-playing', running);
  if (running) tourFrame = requestAnimationFrame(tourTick);
}
new IntersectionObserver(([entry]) => { tourVisible = entry.isIntersecting; syncTour(); }, { threshold: .15 }).observe(tour);
document.addEventListener('visibilitychange', syncTour);
addEventListener('resize', renderTour);
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
if (!['workspace','access','inventory'].includes(currentPage)) { const request = workspaceRequest; api('/plan').then(() => { if (request === workspaceRequest) setSignedIn(true); }).catch(() => { if (request === workspaceRequest) setSignedIn(false); }); }

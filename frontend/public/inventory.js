/* Inventory quantities stay in one explicitly chosen unit per medicine. */
let inventoryItems = [], inventoryLoad = 0;
const inventoryIcon = name => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${({search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',scan:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M7 7v10m3-10v10m4-10v10m3-10v10"/>',plus:'<path d="M12 5v14M5 12h14"/>',download:'<path d="M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4"/>',package:'<path d="m3 7 9-4 9 4v10l-9 4-9-4Zm0 0 9 4 9-4M12 11v10M7.5 5 16 9"/>'})[name]}</svg>`;

async function loadInventory() {
  const container = $('#inventory-content'), request = ++inventoryLoad;
  container.innerHTML = '<p role="status" class="muted">Loading your inventory…</p>';
  try {
    const data = await api('/inventory');
    if (request !== inventoryLoad || currentPage !== 'inventory') return;
    setSignedIn(true);
    inventoryItems = data.items;
    renderInventory();
  } catch (error) {
    if (request !== inventoryLoad || currentPage !== 'inventory') return;
    if (error.message.includes('Sign in required')) { setSignedIn(false); show('access', true); return; }
    container.innerHTML = `<h1 id="inventory-title">Inventory</h1><p role="alert">${escapeHTML(error.message)}</p><button class="btn secondary" id="inventory-retry">Try again</button>`;
    $('#inventory-retry').onclick = loadInventory;
  }
}

function renderInventory(message = '') {
  $('#inventory-content').innerHTML = `
    <div class="inventory-heading"><div><h1 id="inventory-title">Inventory <span id="inventory-total"></span></h1><p>Your medicines, quantities, and stock units in one place.</p></div>
    <div class="inventory-actions"><button type="button" class="btn secondary" id="inventory-export">${inventoryIcon('download')}Export CSV</button><button type="button" class="btn secondary" id="inventory-add">${inventoryIcon('plus')}Add manually</button><button type="button" class="btn" id="inventory-scan">${inventoryIcon('scan')}Scan barcode</button></div></div>
    <div class="inventory-toolbar"><label class="inventory-search">${inventoryIcon('search')}<input type="search" id="inventory-search" aria-label="Search inventory" placeholder="Search medicine, strength, or barcode"></label>
    <label class="inventory-filter">Stock<select id="inventory-stock"><option value="all">All stock</option><option value="low">Low stock</option><option value="out">Out of stock</option><option value="available">In stock</option></select></label>
    <label class="inventory-filter">View<select id="inventory-view"><option value="active">Active medicines</option><option value="archived">Archived medicines</option></select></label>
    <label class="inventory-filter inventory-sort">Sort by<select id="inventory-sort"><option value="name">Medicine A–Z</option><option value="updated">Recently updated</option></select></label></div>
    <p id="inventory-message" class="inventory-message" role="status" aria-live="polite">${escapeHTML(message)}</p>
    <div class="inventory-layout"><div id="inventory-list"><div class="inventory-table-scroll" tabindex="0" role="region" aria-label="Medicines table; scroll horizontally to see all columns"><table class="inventory-table"><caption class="inventory-sr">Pharmacy inventory. Select a medicine to edit its details or quantity.</caption><thead><tr><th scope="col">Medicine</th><th scope="col">Manufacturer</th><th scope="col">Stock unit</th><th scope="col" class="inventory-numeric">On hand</th><th scope="col">Status</th><th scope="col">Updated</th><th scope="col"><span class="inventory-sr">Actions</span></th></tr></thead><tbody id="inventory-rows"></tbody></table></div>
    <div id="inventory-empty" class="inventory-empty" hidden></div><div class="inventory-table-footer"><span id="inventory-count" role="status"></span><div><button type="button" id="inventory-previous" aria-label="Previous inventory page">Previous</button><span id="inventory-page"></span><button type="button" id="inventory-next" aria-label="Next inventory page">Next</button></div></div></div>
    <aside id="inventory-editor" class="inventory-editor" aria-labelledby="inventory-editor-title" hidden></aside></div>
    <p class="inventory-footnote">Quantities use each medicine’s listed unit. Set a low-stock threshold to flag items that need attention.</p>`;
  let page = 0, filtered = [];
  const status = item => item.archived ? 'Archived' : item.quantity === 0 ? 'Out of stock' : item.quantity <= item.low_stock ? 'Low stock' : 'In stock';
  function draw(reset = false) {
    if (reset) page = 0;
    const query = $('#inventory-search').value.trim().toLowerCase(), filter = $('#inventory-stock').value, archived = $('#inventory-view').value === 'archived';
    filtered = inventoryItems.filter(item => Boolean(item.archived) === archived && [item.name,item.strength,item.form,item.manufacturer,item.identifier].join(' ').toLowerCase().includes(query)
      && (filter === 'all' || (filter === 'out' && item.quantity === 0) || (filter === 'low' && item.quantity > 0 && item.quantity <= item.low_stock) || (filter === 'available' && item.quantity > item.low_stock)));
    filtered.sort($('#inventory-sort').value === 'updated' ? (a,b) => b.updated_at.localeCompare(a.updated_at) : (a,b) => a.name.localeCompare(b.name) || a.strength.localeCompare(b.strength));
    page = Math.max(0, Math.min(page, Math.ceil(filtered.length/25)-1));
    $('#inventory-total').textContent = inventoryItems.filter(item => !item.archived).length;
    $('#inventory-rows').innerHTML = filtered.slice(page*25,page*25+25).map(item => `<tr><th scope="row"><button class="inventory-name" data-edit="${item.id}">${escapeHTML(item.name)}</button><small>${escapeHTML([item.strength,item.form].filter(Boolean).join(' · ') || 'No strength or form recorded')}</small>${item.identifier ? `<small class="inventory-identifier">${escapeHTML(item.identifier)}</small>` : ''}</th><td>${escapeHTML(item.manufacturer || '—')}</td><td>${escapeHTML(item.unit)}</td><td class="inventory-numeric">${Number(item.quantity).toLocaleString(undefined, {maximumFractionDigits:6})}</td><td><span class="inventory-badge ${item.archived ? 'is-archived' : item.quantity === 0 ? 'is-out' : item.quantity <= item.low_stock ? 'is-low' : 'is-available'}">${status(item)}</span></td><td class="inventory-date">${escapeHTML(item.updated_at.slice(0,10))}</td><td><button type="button" class="inventory-edit" data-edit="${item.id}" aria-label="Edit ${escapeHTML(item.name)}">Edit</button></td></tr>`).join('');
    const empty = $('#inventory-empty');
    empty.hidden = filtered.length > 0;
    if (!filtered.length) empty.innerHTML = inventoryItems.length ? `<h2>No matching medicines</h2><p>Try another search or change the stock and view filters.</p><button type="button" class="btn secondary" id="inventory-clear">Clear filters</button>` : `${inventoryIcon('package')}<h2>A place for every medicine.</h2><p>Add your first medicine and choose how you count it.<br>Your inventory will stay saved in your pharmacy workspace.</p><button type="button" class="btn" id="inventory-first">Add your first medicine</button>`;
    $('#inventory-list .inventory-table-scroll').hidden = !filtered.length;
    $('#inventory-count').textContent = filtered.length ? `${page*25+1}–${Math.min(page*25+25,filtered.length)} of ${filtered.length} medicines` : '0 medicines';
    $('#inventory-page').textContent = `Page ${page+1}`;
    $('#inventory-previous').disabled = page === 0;
    $('#inventory-next').disabled = (page+1)*25 >= filtered.length;
    $('#inventory-export').disabled = !filtered.length;
    if ($('#inventory-first')) $('#inventory-first').onclick = showBarcodeScanner;
    if ($('#inventory-clear')) $('#inventory-clear').onclick = () => { $('#inventory-search').value = ''; $('#inventory-stock').value = 'all'; $('#inventory-view').value = 'active'; draw(true); };
  }
  $('#inventory-add').onclick = () => editInventory();
  $('#inventory-scan').onclick = showBarcodeScanner;
  $('#inventory-search').oninput = () => draw(true);
  ['stock','view','sort'].forEach(id => { $('#inventory-'+id).onchange = () => draw(true); });
  $('#inventory-previous').onclick = () => { page--; draw(); };
  $('#inventory-next').onclick = () => { page++; draw(); };
  $('#inventory-rows').onclick = event => { const button = event.target.closest('[data-edit]'); if (button) editInventory(inventoryItems.find(item => item.id === button.dataset.edit)); };
  $('#inventory-export').onclick = () => {
    const columns = ['name','strength','form','manufacturer','identifier','unit','quantity','low_stock','archived','updated_at'];
    const cell = value => { let text = String(value ?? ''); if (/^[=+\-@\t\r\n]/.test(text)) text = "'"+text; return '"'+text.replaceAll('"','""')+'"'; };
    const blob = new Blob(['\ufeff'+[columns,...filtered.map(item => columns.map(key => item[key]))].map(row => row.map(cell).join(',')).join('\r\n')], {type:'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob), link = document.createElement('a'); link.href = url; link.download = 'pulse-inventory.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
  };
  draw();
}

function editInventory(item, scannedIdentifier = '', scanMessage = '') {
  const panel = $('#inventory-editor');
  panel.hidden = false;
  const field = (name,label,max,required=false) => `<label>${label}<input name="${name}" value="${escapeHTML(item?.[name] || (name === 'identifier' ? scannedIdentifier : ''))}" maxlength="${max}" ${required ? 'required' : ''}></label>`;
  panel.innerHTML = `<div class="inventory-editor-head"><h2 id="inventory-editor-title">${item ? 'Edit medicine' : 'Add medicine'}</h2><button type="button" id="inventory-close">Close</button></div><p class="inventory-editor-help">${item ? 'Update details or set the current quantity on hand.' : 'Choose the unit you use to count this medicine. Keep future entries in that unit.'}</p>
    <p class="inventory-scan-result" role="status">${escapeHTML(scanMessage)}</p><form id="inventory-form"><fieldset>${field('name','Medicine name',160,true)}<div class="inventory-form-pair">${field('strength','Strength (optional)',80)}${field('form','Dosage form (optional)',80)}</div>${field('manufacturer','Manufacturer (optional)',160)}${field('identifier','Barcode / product code (optional)',100)}
    <label>Inventory unit<input name="unit" list="inventory-units" value="${escapeHTML(item?.unit || '')}" maxlength="40" placeholder="e.g. bottles, boxes, tablets" required ${item ? 'readonly' : ''} aria-describedby="inventory-unit-help"></label><datalist id="inventory-units"><option value="bottles"><option value="boxes"><option value="tablets"><option value="packs"><option value="vials"><option value="mL"></datalist><small id="inventory-unit-help">${item ? 'The unit is fixed to keep quantities consistent.' : 'All quantities below use this unit.'}</small>
    <div class="inventory-form-pair"><label>Quantity on hand<input name="quantity" type="number" min="0" max="1000000000000" step="any" value="${item?.quantity ?? 0}" required></label><label>Low-stock threshold<input name="low_stock" type="number" min="0" max="1000000000000" step="any" value="${item?.low_stock ?? 0}" required></label></div>
    ${item ? `<label class="inventory-archive"><input type="checkbox" name="archived" ${item.archived ? 'checked' : ''}>Archive this medicine</label><small>Archived medicines stay saved and can be restored.</small>` : ''}
    <div class="inventory-form-actions"><button type="submit" class="btn">${item ? 'Save changes' : 'Add medicine'}</button><button type="button" class="btn secondary" id="inventory-cancel">Cancel</button></div></fieldset><p id="inventory-form-error" role="alert"></p></form>`;
  const close = () => { panel.hidden = true; $('#inventory-add').focus(); };
  $('#inventory-close').onclick = close; $('#inventory-cancel').onclick = close;
  panel.onkeydown = event => { if (event.key === 'Escape' && !$('#inventory-form fieldset').disabled) close(); };
  panel.scrollIntoView({block:'nearest'}); panel.querySelector('input').focus({preventScroll:true});
  $('#inventory-form').onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget, payload = Object.fromEntries(new FormData(form));
    payload.quantity = Number(payload.quantity); payload.low_stock = Number(payload.low_stock);
    payload.archived = form.elements.archived?.checked || false; payload.version = item?.version || 0;
    const generation = inventoryLoad;
    form.querySelector('fieldset').disabled = true; $('#inventory-close').disabled = true;
    $('#inventory-form-error').textContent = '';
    try {
      const saved = await api('/inventory'+(item ? '/'+item.id : ''), {method:item ? 'PUT' : 'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if (!signedIn || generation !== inventoryLoad || !form.isConnected) return;
      if (item) inventoryItems = inventoryItems.map(row => row.id === saved.id ? saved : row); else inventoryItems.push(saved);
      renderInventory(`${saved.name} ${item ? 'updated' : 'added'}.`); $('#inventory-add').focus();
    } catch (error) { if (form.isConnected) $('#inventory-form-error').textContent = error.message; }
    finally { form.querySelector('fieldset').disabled = false; if ($('#inventory-close')) $('#inventory-close').disabled = false; }
  };
}

function showBarcodeScanner() {
  const panel = $('#inventory-editor');
  panel.hidden = false;
  panel.innerHTML = `<div class="inventory-editor-head"><h2 id="inventory-editor-title">Scan a medicine</h2><button type="button" id="inventory-scan-close">Close</button></div>
    <p class="inventory-editor-help">Connect a USB or Bluetooth scanner in keyboard mode. Click the field below, then scan the package. Use a 2D scanner for GS1 DataMatrix.</p>
    <form id="inventory-scan-form"><label for="inventory-barcode-input">Scan barcode</label><input id="inventory-barcode-input" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="512" aria-describedby="inventory-scanner-help" required>
    <small id="inventory-scanner-help">Configure the scanner to send Enter after each scan, or select Read barcode.</small>
    <div class="inventory-form-actions"><button class="btn" type="submit">Read barcode</button><button class="btn secondary" id="inventory-scan-manual" type="button">Enter manually</button></div>
    <p id="inventory-scan-error" role="alert"></p></form>`;
  const input = $('#inventory-barcode-input');
  const close = () => { panel.hidden = true; $('#inventory-scan').focus(); };
  $('#inventory-scan-close').onclick = close;
  panel.onkeydown = event => { if (event.key === 'Escape') close(); };
  $('#inventory-scan-manual').onclick = () => editInventory();
  $('#inventory-scan-form').onsubmit = event => {
    event.preventDefault();
    try {
      const code = productBarcode(input.value);
      const matches = inventoryItems.filter(item => {
        if (!item.identifier) return false;
        try { return productBarcode(item.identifier) === code; } catch { return item.identifier === code; }
      });
      if (matches.length > 1) throw Error('This barcode is linked to more than one medicine. Close scanning and search inventory to choose the correct entry.');
      if (matches.length) {
        editInventory(matches[0], '', matches[0].archived ? 'Barcode matched an archived medicine. Review its details and restore it if needed.' : 'Barcode matched. Review the saved details and update the quantity on hand.');
      } else {
        showBarcodeLink(code);
      }
    } catch (error) { $('#inventory-scan-error').textContent = error.message; input.select(); }
  };
  panel.scrollIntoView({block:'nearest'}); input.focus({preventScroll:true});
}

function showBarcodeLink(code) {
  const panel = $('#inventory-editor');
  panel.innerHTML = `<div class="inventory-editor-head"><h2 id="inventory-editor-title">Link this barcode</h2><button type="button" id="inventory-link-close">Close</button></div>
    <p class="inventory-editor-help">Scanned product code <strong>${escapeHTML(code)}</strong>. Link it to a saved medicine or add its details once. Future scans can find those details.</p>
    <form id="inventory-link-form"><label for="inventory-link-medicine">Existing medicine</label><select id="inventory-link-medicine" required><option value="">Choose a medicine</option>${inventoryItems.filter(item => !item.archived && !item.identifier).map(item => `<option value="${item.id}">${escapeHTML([item.name,item.strength,item.unit].filter(Boolean).join(' · '))}</option>`).join('')}</select>
    <div class="inventory-form-actions"><button class="btn" type="submit" ${inventoryItems.some(item => !item.archived && !item.identifier) ? '' : 'disabled'}>Link medicine</button><button class="btn secondary" id="inventory-link-new" type="button">Add new medicine</button></div></form>`;
  const close = () => { panel.hidden = true; $('#inventory-scan').focus(); };
  $('#inventory-link-close').onclick = close;
  panel.onkeydown = event => { if (event.key === 'Escape') close(); };
  $('#inventory-link-new').onclick = () => editInventory(undefined, code, 'Barcode captured. Add product details once to recognize future scans.');
  $('#inventory-link-form').onsubmit = event => {
    event.preventDefault();
    const item = inventoryItems.find(row => row.id === $('#inventory-link-medicine').value);
    if (item) editInventory({...item, identifier:code}, '', 'Barcode linked in this form. Save changes to remember it for future scans.');
  };
  $('#inventory-link-new').focus();
}

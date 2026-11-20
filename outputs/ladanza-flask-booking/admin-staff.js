/* Staff data stays in memory; customer confirmation links are never saved locally here. */
(() => {
  'use strict';
  let customer = null, editing = null, preference = null, draft = null, receipt = null;
  let requestNumber = 0, slotNumber = 0, selectionNumber = 0, submitting = false, uncertain = false, savingCustomer = false;
  const groupMenus = new Set(['サロン', 'チャーター 30分', 'テーマパークダンス 60分', '社会人サークル 60分']);
  const show = (id, visible) => $('#'+id).classList.toggle('hidden', !visible);
  const status = (message, error = false) => { $('#staffStatus').textContent = message; $('#staffStatus').classList.toggle('error-text', error); };
  const random = () => [...crypto.getRandomValues(new Uint8Array(20))].map(v => v.toString(16).padStart(2, '0')).join('');
  const title = menu => menuLabels[menu] || menu;
  async function api(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options, headers: headers() });
    let data;
    try { data = await response.json(); } catch { throw new Error('結果を確認できませんでした。通信状況をご確認ください。'); }
    if (!response.ok) { const error = new Error(response.status === 401 ? 'ログインし直してください。' : data.detail || '処理を完了できませんでした。'); error.status = response.status; throw error; }
    return data;
  }
  function blocked() {
    if (savingCustomer) {status('お客様情報を保存しています。少々お待ちください。');return true;}
    if (!submitting && !uncertain) return false;
    status('予約結果を確認中です。「同じ予約の結果を再確認」から先に確認してください。', true); return true;
  }
  function resetDraft() {
    draft = null; uncertain = false; $('#proxyConfirmed').checked = false;
    $('#proxyConfirmed').disabled = false; $('#confirmProxyBooking').disabled = false;
    $('#backProxyBooking').disabled = false; $('#confirmProxyBooking').textContent = 'この内容で予約を確定';
    $('#proxyError').textContent = ''; show('proxyReview', false);
  }
  function renderCustomer() {
    $('#selectedCustomer').replaceChildren(node('strong', customer.name + ' 様'), node('p', '登録電話番号：' + customer.phone));
    if (customer.note) $('#selectedCustomer').append(node('p', customer.note));
  }
  function focusBookingOnPhone() {
    if (window.innerWidth <= 760) $('#proxyBooking').scrollIntoView?.({block:'start', behavior:'smooth'});
  }
  function applyMenus() {
    const previous = $('#proxyMenu').value;
    $('#proxyMenu').replaceChildren();
    for (const menu of Object.keys(config.menus)) { const option = node('option', title(menu)); option.value = menu; $('#proxyMenu').append(option); }
    $('#proxyMenu').value = preference && config.menus[preference.menu] ? preference.menu : previous || 'サロン';
    updateInstructors();
    if (preference && [...$('#proxyInstructor').options].some(o => o.value === preference.instructor)) $('#proxyInstructor').value = preference.instructor;
  }
  function updateInstructors() {
    const old = $('#proxyInstructor').value;
    const names = groupMenus.has($('#proxyMenu').value) ? ['スタジオ主催'] : ['大村 尊', '大村 友恵', '廣瀬 裕貴'];
    $('#proxyInstructor').replaceChildren(...names.map(name => { const option = node('option', name); option.value = name; return option; }));
    if (names.includes(old)) $('#proxyInstructor').value = old;
  }
  async function selectCustomer(id) {
    if (blocked()) return;
    const number = ++selectionNumber;
    status('お客様情報を確認しています…');
    try {
      const data = await api('/api/admin/customers/' + encodeURIComponent(id));
      if (number !== selectionNumber) return;
      customer = data; resetDraft(); renderCustomer(); applyMenus();
      show('customerEditor', false); show('proxyResult', false); show('proxyBooking', true); show('staffWork', true);
      $('#proxyFields').disabled = false; status(''); await loadSlots(); focusBookingOnPhone();
    } catch (error) { if (number === selectionNumber) status(error.message, true); }
  }
  async function loadCustomers() {
    const number = ++requestNumber;
    $('#customerList').replaceChildren(node('p', '名簿を読み込んでいます…'));
    $('#customerHistory').replaceChildren();
    try {
      const data = await api('/api/admin/customers?' + new URLSearchParams({q: $('#customerQuery').value.trim()}));
      if (number !== requestNumber) return;
      $('#customerList').replaceChildren();
      if (!data.customers.length) $('#customerList').append(node('p', '該当する登録はありません。「予約履歴から探す」で以前の予約を選ぶか、新しく登録してください。', 'hint'));
      for (const row of data.customers) {
        const button = node('button', undefined, 'customer-choice'); button.type = 'button';
        button.append(node('strong', (row.favorite ? '★ ' : '') + row.name + ' 様'));
        if (row.kana) button.append(node('span', row.kana));
        button.append(node('span', '電話番号末尾 ' + row.phone.slice(-4) + (row.note ? ' ／ ' + row.note : '')));
        button.onclick = () => { preference = null; selectCustomer(row.id); };
        $('#customerList').append(button);
      }
    } catch (error) { if (number === requestNumber) $('#customerList').replaceChildren(node('p', error.message, 'error-text')); }
  }
  async function history() {
    if (blocked()) return;
    const number = ++requestNumber;
    $('#customerHistory').replaceChildren(node('p', '予約履歴を調べています…'));
    try {
      const data = await api('/api/admin/customer-history?' + new URLSearchParams({q: $('#customerQuery').value.trim()}));
      if (number !== requestNumber) return;
      $('#customerHistory').replaceChildren(node('h3', '予約履歴から選ぶ'), node('p', data.from + '〜' + data.to + 'の予約。番号を確認してから名簿に登録します。', 'hint'));
      if (!data.candidates.length) $('#customerHistory').append(node('p', '該当する予約はありません。新しいお客様として登録できます。'));
      if (data.total > data.candidates.length) $('#customerHistory').append(node('p', '該当 ' + data.total + '件のうち新しい50件を表示しています。お名前を追加して絞り込めます。', 'hint'));
      for (const row of data.candidates) {
        const button = node('button', undefined, 'customer-choice'); button.type = 'button';
        button.append(node('strong', row.name + ' 様'), node('span', LaDanzaReservations.format(row.starts_at)),
          node('span', title(row.menu) + ' ／ 電話番号末尾 ' + (row.phone.slice(-4) || '記録なし') + (row.status === 'cancelled' ? ' ／ キャンセル済み' : '')));
        button.onclick = () => fromBooking(row.event_id); $('#customerHistory').append(button);
      }
    } catch (error) { if (number === requestNumber) $('#customerHistory').replaceChildren(node('p', error.message, 'error-text')); }
  }
  function edit(row = {}) {
    if (blocked()) return;
    ++selectionNumber; ++slotNumber; resetDraft();
    editing = row.id ? {...row} : {id: 'c1' + random().slice(0, 32)};
    $('#customerEditorTitle').textContent = row.revision ? 'お客様情報の修正' : 'お客様情報の登録';
    for (const [id, key] of [['customerName','name'],['customerKana','kana'],['customerPhone','phone'],['customerNote','note']]) $('#'+id).value = row[key] || '';
    $('#customerFavorite').checked = row.favorite === true; $('#contactConfirmed').checked = false;
    $('#customerError').textContent = ''; $('#customerPhone').setCustomValidity('');
    $('#saveCustomer').textContent = row.revision ? '保存して予約に進む' : '登録して予約に進む';
    show('staffWork', true); show('customerEditor', true); show('proxyBooking', false); show('proxyResult', false);
    $('#customerName').focus();
  }
  async function saveCustomer(event) {
    event.preventDefault(); if (blocked() || !editing) return;
    const phone = LaDanzaReservations.normalizePhone($('#customerPhone').value); $('#customerPhone').value = phone;
    $('#customerPhone').setCustomValidity(LaDanzaReservations.validPhone(phone) ? '' : '電話番号は10桁または11桁の数字で入力してください。');
    if (!$('#customerForm').reportValidity()) return;
    const payload = {id: editing.id, revision: editing.revision, name: $('#customerName').value.trim(), kana: $('#customerKana').value.trim(), phone,
      note: $('#customerNote').value.trim(), favorite: $('#customerFavorite').checked, contact_confirmed: $('#contactConfirmed').checked};
    savingCustomer = true; $('#customerFields').disabled = true; $('#customerError').textContent = '';
    try {
      const row = await api('/api/admin/customers' + (editing.revision ? '/' + encodeURIComponent(editing.id) : ''), {method: editing.revision ? 'PUT' : 'POST', body: JSON.stringify(payload)});
      // Keep the successful record even if refreshing the list subsequently fails.
      customer = row; resetDraft(); renderCustomer(); applyMenus();
      show('customerEditor', false); show('proxyBooking', true); $('#proxyFields').disabled = false;
      $('#customerQuery').value = ''; status('お客様情報を保存しました。次回からは名前を選ぶだけで予約できます。');
      await Promise.all([loadCustomers(), loadSlots()]); focusBookingOnPhone();
    } catch (error) { $('#customerError').textContent = error.message; }
    finally { savingCustomer = false; $('#customerFields').disabled = false; }
  }
  async function loadSlots() {
    const number = ++slotNumber;
    resetDraft(); $('#proxyTime').replaceChildren(new Option('空き時間を確認中…', ''));
    $('#proxyTime').disabled = true; $('#reviewProxyBooking').disabled = true;
    if (!customer || !$('#proxyDate').value) return;
    $('#proxySlotStatus').textContent = '最新の空き時間を確認しています…';
    try {
      const rows = await api('/api/admin/booking-slots?' + new URLSearchParams({date: $('#proxyDate').value, menu: $('#proxyMenu').value, instructor: $('#proxyInstructor').value}));
      if (number !== slotNumber) return;
      const available = rows.filter(row => row.available);
      $('#proxyTime').replaceChildren(new Option(available.length ? '開始時刻を選択' : '空き時間がありません', ''));
      for (const row of available) {
        const time = new Intl.DateTimeFormat('ja-JP', {timeZone:'Asia/Tokyo', hour:'2-digit', minute:'2-digit'}).format(new Date(row.starts_at));
        const end = new Intl.DateTimeFormat('ja-JP', {timeZone:'Asia/Tokyo', hour:'2-digit', minute:'2-digit'}).format(new Date(new Date(row.starts_at).getTime() + row.duration_minutes * 60000));
        $('#proxyTime').append(new Option(time + '〜' + end + (row.capacity > 1 ? '（残り' + row.remaining + '名）' : ''), row.starts_at));
      }
      $('#proxyTime').disabled = !available.length;
      $('#proxySlotStatus').textContent = available.length ? '確定時に空き状況をもう一度確認します。' : 'この日は受付枠がないか、満席・受付終了です。別の日付をご確認ください。';
    } catch (error) { if (number === slotNumber) { $('#proxyTime').replaceChildren(new Option('空き状況を確認できません', '')); $('#proxySlotStatus').textContent = error.message; } }
  }
  function review(event) {
    event.preventDefault(); if (blocked() || !customer || !$('#proxyForm').reportValidity()) return;
    draft = {customer_id: customer.id, customer_revision: customer.revision, menu: $('#proxyMenu').value,
      instructor: $('#proxyInstructor').value, starts_at: $('#proxyTime').value, request_key: random(), booking_confirmed: true};
    $('#proxyReviewDetails').replaceChildren(node('strong', customer.name + ' 様'), node('p', '電話番号：' + customer.phone),
      node('p', LaDanzaReservations.format(draft.starts_at)), node('p', title(draft.menu) + ' ／ ' + draft.instructor));
    $('#proxyConfirmed').checked = false; $('#proxyError').textContent = ''; $('#proxyFields').disabled = true;
    show('proxyReview', true); $('#proxyReviewTitle').scrollIntoView?.({block:'nearest'});
  }
  async function confirm() {
    if (submitting || !draft) return;
    if (!$('#proxyConfirmed').checked) { $('#proxyError').textContent = '予約依頼と内容を確認し、チェックを入れてください。'; return; }
    submitting = true; $('#confirmProxyBooking').disabled = true; $('#backProxyBooking').disabled = true; $('#proxyConfirmed').disabled = true;
    $('#proxyError').textContent = ''; $('#confirmProxyBooking').textContent = '予約を確認しています…';
    try {
      const result = await api('/api/admin/bookings', {method:'POST', body:JSON.stringify(draft)});
      receipt = {...result, menu: draft.menu, instructor: draft.instructor, name: customer.name};
      uncertain = false; $('#staffConfirmationLink').value = result.reservation_url;
      $('#staffReceipt').textContent = 'La Danza ご予約のご案内\n\n' + customer.name + ' 様\n' + LaDanzaReservations.format(result.starts_at)
        + '\n' + title(draft.menu) + ' ／ ' + draft.instructor + '\n予約番号：' + result.reservation_number;
      $('#staffCopyStatus').textContent = ''; show('staffWork', false); show('proxyResult', true); status('');
      draft = null;
    } catch (error) {
      uncertain = !error.status || error.status >= 500;
      $('#proxyError').textContent = error.message + (uncertain ? ' 同じ内容・同じ受付番号で再確認できます。別の予約を作らず、下のボタンを押してください。' : ' 内容を修正する場合は「戻って修正」を押してください。');
      $('#confirmProxyBooking').textContent = uncertain ? '同じ予約の結果を再確認' : 'この内容で予約を確定';
      $('#backProxyBooking').disabled = uncertain; $('#proxyConfirmed').disabled = uncertain;
    } finally { submitting = false; $('#confirmProxyBooking').disabled = false; }
  }
  async function open() {
    if (!switchAdminView('staff')) return false;
    if (!$('#proxyDate').value) $('#proxyDate').value = japanToday();
    $('#proxyDate').min = japanToday(); const last = new Date(japanToday()+'T00:00:00Z'); last.setUTCDate(last.getUTCDate()+365); $('#proxyDate').max = last.toISOString().slice(0,10);
    if (!draft) await loadCustomers(); return true;
  }
  async function fromBooking(eventId) {
    if (blocked() || !(await open())) return;
    const number = ++selectionNumber; status('前回の予約情報を確認しています…');
    try {
      const row = await api('/api/admin/bookings/'+encodeURIComponent(eventId)+'/customer');
      if (number !== selectionNumber) return;
      preference = row;
      if (row.customer_id) await selectCustomer(row.customer_id);
      else { edit(row); status('初回だけ正しい電話番号を確認して名簿に登録してください。すでに登録済みなら、左の名簿から同じお客様を選んでください。'); }
    } catch (error) { if (number === selectionNumber) status(error.message, true); }
  }
  async function copy(value, field) {
    try { await navigator.clipboard.writeText(value); $('#staffCopyStatus').textContent = 'コピーしました。お客様へのご案内に貼り付けてください。'; }
    catch { if (field) {field.focus();field.select();} else { const selection=window.getSelection(),range=document.createRange();range.selectNodeContents($('#staffReceipt'));selection.removeAllRanges();selection.addRange(range); } $('#staffCopyStatus').textContent = '選択した内容を長押しするか、コピー操作でコピーしてください。'; }
  }
  $('#customerSearchForm').onsubmit = event => { event.preventDefault(); if (!blocked()) loadCustomers(); };
  $('#searchHistory').onclick = history;
  $('#newCustomer').onclick = () => { if (!blocked()) {preference=null;edit();} };
  $('#customerForm').onsubmit = saveCustomer;
  $('#customerPhone').oninput = () => $('#customerPhone').setCustomValidity('');
  $('#closeCustomerEditor').onclick = () => {show('customerEditor', false);show('proxyBooking', !!customer);};
  $('#editCustomer').onclick = () => customer && edit(customer);
  $('#proxyMenu').onchange = () => {updateInstructors();loadSlots();}; $('#proxyInstructor').onchange = loadSlots; $('#proxyDate').onchange = loadSlots;
  $('#reloadProxySlots').onclick = loadSlots; $('#proxyTime').onchange = () => {resetDraft();$('#reviewProxyBooking').disabled = !$('#proxyTime').value;};
  $('#proxyForm').onsubmit = review;
  $('#backProxyBooking').onclick = () => {if (!blocked()) {resetDraft();$('#proxyFields').disabled=false;}};
  $('#confirmProxyBooking').onclick = confirm;
  $('#copyStaffLink').onclick = () => copy($('#staffConfirmationLink').value, $('#staffConfirmationLink'));
  $('#copyStaffMemo').onclick = () => copy($('#staffReceipt').textContent);
  $('#printStaffMemo').onclick = () => window.print();
  $('#nextProxyBooking').onclick = () => {resetDraft();$('#proxyFields').disabled=false;show('proxyResult',false);show('staffWork',true);loadSlots();loadCustomers();};
  $('#viewProxyBooking').onclick = () => {if (receipt) {switchAdminView('bookings');selectBookingDate(receipt.starts_at.slice(0,10));}};
  window.addEventListener('beforeunload', event => {if (submitting || uncertain) {event.preventDefault();event.returnValue='';}});
  window.LaDanzaStaff = {open, fromBooking};
})();

(() => {
  'use strict';
  const R = window.LaDanzaReservations, $ = s => document.querySelector(s);
  let generation = 0;
  const node = (tag, text, cls) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; };
  function details(item) {
    const card = node('article', undefined, 'card');
    card.append(node('div', '予約番号：' + item.reservation_number, 'number'), node('p', R.format(item.starts_at), 'date'), node('p', [item.menu, item.instructor].filter(Boolean).join(' ／ '), 'detail'), node('p', item.status === 'cancelled' ? 'キャンセル済み' : '予約確定', 'status' + (item.status === 'cancelled' ? ' cancelled' : '')));
    return card;
  }
  function forgetButton(saved, card) {
    const button = node('button', 'このスマホの一覧から外す', 'forget'); button.type = 'button';
    button.onclick = () => { if (!confirm('このスマホの一覧から外しますか？\n予約そのものはキャンセルされません。確認リンクを保管してから外してください。')) return; if (R.forget(saved.token)) loadSaved(); else alert('保存情報を変更できませんでした。'); };
    card.append(button);
  }
  async function loadSaved() {
    const current = ++generation, saved = R.read();
    $('#savedBookings').replaceChildren(); $('#savedBookings').setAttribute('aria-busy', 'true'); $('#refresh').disabled = true;
    if (!saved.items.length) {
      $('#savedMessage').textContent = saved.available ? 'このブラウザには、まだ予約が保存されていません。以前の予約は下の電話番号検索で探せます。確認リンクをお持ちの場合は、リンクを開くとこの一覧に保存されます。' : 'このブラウザでは保存情報を読み込めません。お持ちの確認リンク、または下の電話番号検索をご利用ください。';
      $('#savedMessage').className = 'empty'; $('#phoneSearch').open = true; $('#savedBookings').setAttribute('aria-busy', 'false'); $('#refresh').disabled = false; return;
    }
    $('#savedMessage').textContent = '最新の予約状況を確認しています…'; $('#savedMessage').className = 'notice';
    const results = [];
    for (let i = 0; i < saved.items.length; i += 4) {
      const batch = await Promise.all(saved.items.slice(i, i + 4).map(async item => {
        try {
          const response = await fetch('/api/reservations/' + encodeURIComponent(item.token), { cache: 'no-store' });
          if (!response.ok) return { saved: item, error: response.status === 404 ? '確認リンクが無効になっています。下のお問い合わせからご連絡ください。' : '最新の状況を取得できませんでした。時間をおいて更新してください。' };
          const data = await response.json(); if (!data.reservation_number || !Number.isFinite(Date.parse(data.starts_at))) throw new Error('invalid response');
          return { saved: item, data };
        } catch { return { saved: item, error: '通信できませんでした。予約の有無は判断できません。接続を確認して更新してください。' }; }
      }));
      if (current !== generation) return; results.push(...batch);
    }
    results.sort((a, b) => (a.data?.starts_at || a.saved.starts_at).localeCompare(b.data?.starts_at || b.saved.starts_at));
    for (const result of results) {
      let card;
      if (result.data) { card = details(result.data); const link = node('a', '予約内容・キャンセル', 'button'); link.href = R.url(result.saved.token); card.append(link); }
      else { card = node('article', undefined, 'card'); card.append(node('div', '予約番号：' + (result.saved.reservation_number || '確認できません'), 'number'), node('p', result.error, 'message')); }
      forgetButton(result.saved, card); $('#savedBookings').append(card);
    }
    const errors = results.filter(result => result.error).length;
    $('#savedMessage').textContent = errors ? `${results.length}件中${errors}件は最新状況を確認できませんでした。` : `${results.length}件の保存済み予約を確認しました。過去・キャンセル済みの予約も表示しています。`;
    $('#savedBookings').setAttribute('aria-busy', 'false'); $('#refresh').disabled = false;
  }
  $('#refresh').onclick = loadSaved;
  $('#contact').oninput = () => $('#contact').setCustomValidity('');
  $('#form').onsubmit = async event => {
    event.preventDefault(); const contact = R.normalizePhone($('#contact').value); $('#contact').value = contact;
    $('#contact').setCustomValidity(R.validPhone(contact) ? '' : '電話番号を10桁または11桁の数字で入力してください。'); if (!event.currentTarget.reportValidity()) return;
    $('#submit').disabled = true; $('#submit').textContent = '確認しています…'; $('#message').classList.add('hidden'); $('#result').classList.add('hidden'); $('#bookings').replaceChildren();
    try {
      const response = await fetch('/api/reservations/lookup-all', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ contact }), cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw new Error(response.status === 404 ? 'この電話番号では予約が見つかりませんでした。番号を間違えて予約した可能性がある方は、下の案内をご確認ください。' : data.detail || '予約を確認できませんでした。');
      if (!Array.isArray(data.bookings)) throw new Error('予約を確認できませんでした。');
      $('#count').textContent = data.bookings.length + '件の予約があります';
      for (const item of data.bookings) {
        const card = details(item), button = node('button', item.status === 'cancelled' ? 'キャンセル済み' : 'この予約をキャンセルする', 'button'); button.type = 'button'; button.disabled = item.status === 'cancelled';
        button.onclick = async () => {
          if (!confirm('この予約をキャンセルしますか？\n取り消し後は元に戻せません。')) return; button.disabled = true;
          try { const r = await fetch('/api/reservations/lookup/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ contact, reservation_number: item.reservation_number }) }); if (!r.ok) throw new Error('処理を確認できませんでした。予約状況を再確認してください。'); card.querySelector('.status').textContent = 'キャンセル済み'; card.querySelector('.status').classList.add('cancelled'); button.textContent = 'キャンセル済み'; loadSaved(); }
          catch (error) { alert(error.message); button.disabled = false; }
        };
        card.append(button); $('#bookings').append(card);
      }
      $('#result').classList.remove('hidden');
    } catch (error) { $('#message').textContent = error.message; $('#message').classList.remove('hidden'); $('#help').open = true; }
    finally { $('#submit').disabled = false; $('#submit').textContent = '電話番号で予約を探す'; }
  };
  loadSaved();
})();

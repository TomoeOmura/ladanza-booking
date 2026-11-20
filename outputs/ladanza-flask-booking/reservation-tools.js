/* Browser-local confirmation links. No customer names or phone numbers are stored. */
(() => {
  'use strict';
  const key = 'ladanza.reservations.v1';
  const validToken = token => typeof token === 'string' && /^[A-Za-z0-9_-]{30,500}$/.test(token);
  function read() {
    try {
      const raw = localStorage.getItem(key), items = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(items)) throw new Error('invalid storage');
      return { available: true, items: items.filter(item => item && validToken(item.token)).map(item => ({ token: item.token, reservation_number: String(item.reservation_number || ''), starts_at: String(item.starts_at || '') })) };
    } catch { return { available: false, items: [] }; }
  }
  function remember(item) {
    if (!validToken(item.token)) return false;
    const current = read(); if (!current.available) return false;
    const items = current.items.filter(old => old.token !== item.token && (!item.reservation_number || old.reservation_number !== item.reservation_number));
    items.push({ token: item.token, reservation_number: String(item.reservation_number || ''), starts_at: String(item.starts_at || '') });
    try { localStorage.setItem(key, JSON.stringify(items)); return true; } catch { return false; }
  }
  function forget(token) {
    const current = read(); if (!current.available) return false;
    try { localStorage.setItem(key, JSON.stringify(current.items.filter(item => item.token !== token))); return true; } catch { return false; }
  }
  function tokenFromUrl(value) {
    try { const url = new URL(value, location.origin), token = url.searchParams.get('token'); return url.origin === location.origin && url.pathname === '/reservation.html' && validToken(token) ? token : null; } catch { return null; }
  }
  function normalizePhone(value) { return String(value || '').normalize('NFKC').replace(/[\s\-‐‑‒–—―−ー()]/g, ''); }
  async function copy(value, field, message) {
    field.value = value;
    try { await navigator.clipboard.writeText(value); message.textContent = 'リンクをコピーしました。ご自身で保管してください。'; }
    catch { field.hidden = false; field.focus(); field.select(); message.textContent = '下のリンクを長押ししてコピーしてください。'; }
  }
  window.LaDanzaReservations = { read, remember, forget, tokenFromUrl, normalizePhone, copy,
    validPhone: value => /^[0-9]{10,11}$/.test(normalizePhone(value)),
    url: token => '/reservation.html?' + new URLSearchParams({ token }),
    format: value => new Intl.DateTimeFormat('ja-JP', { timeZone: 'Asia/Tokyo', year: 'numeric', month: 'long', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
  };
})();

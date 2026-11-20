(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const drop = $('#drop'), input = $('#fileinput'), form = $('#upform'), prog = $('#prog');
  if (drop) {
    $('#pick').addEventListener('click', () => input.click());
    drop.addEventListener('click', e => { if (e.target === drop) input.click(); });
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', e => { input.files = e.dataTransfer.files; htmx.trigger(form, 'submit'); });
    input.addEventListener('change', () => { if (input.files.length) htmx.trigger(form, 'submit'); });
    form.addEventListener('htmx:xhr:progress', e => { prog.hidden = false; prog.value = (e.detail.loaded / e.detail.total) * 100; });
    form.addEventListener('htmx:afterRequest', () => { setTimeout(() => { prog.hidden = true; prog.value = 0; }, 400); input.value = ''; const em = $('#empty'); if (em) em.remove(); });
  }
  // open the first newly uploaded invoice in the review pane
  document.body.addEventListener('htmx:afterSwap', e => {
    const t = e.target.querySelector && e.target.querySelector('template[data-open]');
    if (t) { htmx.ajax('GET', `/invoices/${t.dataset.open}/review`, { target: '#review', swap: 'innerHTML' }); t.remove(); }
  });
  // delete (works on the review pane and the database table)
  document.body.addEventListener('click', async e => {
    const b = e.target.closest('[data-del]');
    if (!b) return;
    if (!confirm('این فاکتور حذف شود؟')) return;
    const id = b.dataset.del;
    const r = await fetch(`/invoices/${id}`, { method: 'DELETE' });
    if (!r.ok) { alert('حذف نشد'); return; }
    ['#row-' + id, '#r-' + id, '#d-' + id].forEach(s => { const n = $(s); if (n) (n.closest('tr.det') || n).remove(); });
    const rv = $('#review'); if (rv) rv.innerHTML = '<p class="muted placeholder">حذف شد.</p>';
  });
  const reset = $('#reset');
  if (reset) reset.addEventListener('click', async () => {
    if (!confirm('همهٔ آپلودهای شما پاک شود؟ داده‌های نمونه می‌مانند.')) return;
    await fetch('/api/demo/reset', { method: 'POST' });
    location.href = '/upload';
  });
})();

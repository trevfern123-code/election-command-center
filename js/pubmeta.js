(() => {
  // header line: latest verified publication, read from the public manifest (never from the visitor's clock)
  const AZ = 'America/Phoenix';
  const f = (s) => { const d = new Date(s); return isNaN(d) ? '—' : d.toLocaleString('en-US', { timeZone: AZ, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) + ' Arizona time'; };
  window.addEventListener('ec-manifest', (e) => {
    const m = e.detail || {};
    const box = document.getElementById('pub-at');
    if (!box) return;
    box.textContent = `Latest verified publication: ${f(m.published_at)} (hourly run for ${f(m.scheduled_for)}; source coverage ${m.coverage_health || 'unknown'}${m.key_figures_confirmed ? ', ' + m.key_figures_confirmed + ' key figures confirmed' : ''}). An hourly check does not mean a source published new figures: each figure shows its own "data as of" date. This page checks for a newer publication every 5 minutes.`;
  });
})();

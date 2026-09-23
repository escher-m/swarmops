/* Progressive enhancement only. Every page is complete server-rendered HTML;
   nothing here is required to read the ledger. */

(function () {
  'use strict';

  var root = document.documentElement;
  var toggle = document.getElementById('theme-toggle');
  var label = document.getElementById('theme-label');

  function syncLabel() {
    if (label) label.textContent = root.dataset.theme === 'light' ? 'Dark mode' : 'Light mode';
  }
  syncLabel();

  if (toggle) {
    toggle.addEventListener('click', function () {
      var next = root.dataset.theme === 'light' ? 'dark' : 'light';
      root.dataset.theme = next;
      // Private browsing and blocked site data both throw here; the theme still
      // applies for this page view, it just will not be remembered.
      try { localStorage.setItem('swarmops-theme', next); } catch (e) {}
      syncLabel();
      window.dispatchEvent(new CustomEvent('swarmops:theme', { detail: next }));
    });
  }

  // Keyboard shortcuts for people who live in this tool during an incident.
  document.addEventListener('keydown', function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    var go = { g: '/', i: '/incidents', a: '/approvals', v: '/verify' }[e.key];
    if (go) { location.href = go; }
    if (e.key === '/') {
      var search = document.querySelector('input[name="search"]');
      if (search) { e.preventDefault(); search.focus(); }
    }
  });
})();

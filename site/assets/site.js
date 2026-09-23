/* Progressive enhancement. Every page is complete without this file. */
(function () {
  'use strict';
  var root = document.documentElement;
  try { var t = localStorage.getItem('swarmops-theme'); if (t) root.dataset.theme = t; } catch (e) {}

  document.addEventListener('click', function (e) {
    var toggle = e.target.closest('[data-theme-toggle]');
    if (toggle) {
      var next = root.dataset.theme === 'light' ? 'dark' : 'light';
      root.dataset.theme = next;
      try { localStorage.setItem('swarmops-theme', next); } catch (err) {}
    }
    var burger = e.target.closest('.nav-toggle');
    if (burger) document.querySelector('.nav-links').classList.toggle('open');
  });

  // Scroll reveals, text reveal, spotlight, counters and the rest live in
  // motion.js, loaded alongside this file.
})();

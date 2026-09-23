/* Motion primitives, implemented directly rather than pulled in.
 *
 * These are the patterns a component library would give us -- staggered text
 * reveal, cursor spotlight, count-up, typewriter, marquee, tilt, scroll-pinned
 * steps. Written by hand because the site is static HTML with no bundler, and
 * because the page claims to load nothing third-party, which is a claim worth
 * keeping true.
 *
 * Every behaviour is opt-in via a data attribute, every one degrades to the
 * finished state when JavaScript or IntersectionObserver is unavailable, and
 * every one honours prefers-reduced-motion.
 */

(function () {
  'use strict';

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var hasIO = 'IntersectionObserver' in window;

  /* Run fn once, when el first enters the viewport. */
  function onceVisible(el, fn, margin) {
    if (!hasIO) { fn(); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { io.unobserve(e.target); fn(); }
      });
    }, { rootMargin: margin || '0px 0px -12% 0px' });
    io.observe(el);
  }

  // ---------------------------------------------------------- text reveal
  // Splits into words (not characters -- character stagger on a long headline
  // reads as a gimmick and hurts the read) and raises them in sequence.
  document.querySelectorAll('[data-reveal-text]').forEach(function (el) {
    var text = el.textContent.trim();
    if (reduced) return;
    var delay = parseFloat(el.dataset.revealDelay || '0');
    el.textContent = '';
    text.split(/\s+/).forEach(function (word, i) {
      var span = document.createElement('span');
      span.className = 'rv-word';
      span.textContent = word;
      span.style.transitionDelay = (delay + i * 0.045) + 's';
      el.appendChild(span);
      el.appendChild(document.createTextNode(' '));
    });
    onceVisible(el, function () { el.classList.add('rv-in'); }, '0px');
  });

  // ------------------------------------------------------------ spotlight
  // A radial highlight tracking the cursor, written to CSS custom properties
  // so the styling stays in the stylesheet.
  document.querySelectorAll('[data-spotlight]').forEach(function (el) {
    el.addEventListener('pointermove', function (e) {
      var r = el.getBoundingClientRect();
      el.style.setProperty('--mx', (e.clientX - r.left) + 'px');
      el.style.setProperty('--my', (e.clientY - r.top) + 'px');
    });
  });

  // ----------------------------------------------------------------- tilt
  document.querySelectorAll('[data-tilt]').forEach(function (el) {
    if (reduced) return;
    var max = parseFloat(el.dataset.tilt) || 6;
    el.addEventListener('pointermove', function (e) {
      var r = el.getBoundingClientRect();
      var px = (e.clientX - r.left) / r.width - 0.5;
      var py = (e.clientY - r.top) / r.height - 0.5;
      el.style.transform =
        'perspective(900px) rotateX(' + (-py * max) + 'deg) rotateY(' + (px * max) + 'deg)';
    });
    el.addEventListener('pointerleave', function () { el.style.transform = ''; });
  });

  // ------------------------------------------------------------- count up
  document.querySelectorAll('[data-count]').forEach(function (el) {
    var target = parseFloat(el.dataset.count);
    var suffix = el.dataset.countSuffix || '';
    var decimals = parseInt(el.dataset.countDecimals || '0', 10);
    if (reduced || isNaN(target)) { el.textContent = target.toFixed(decimals) + suffix; return; }
    onceVisible(el, function () {
      var dur = 1100, t0 = null;
      (function step(now) {
        if (t0 === null) t0 = now;
        var p = Math.min((now - t0) / dur, 1);
        // Ease-out cubic: fast start, settles on the number.
        var eased = 1 - Math.pow(1 - p, 3);
        el.textContent = (target * eased).toFixed(decimals) + suffix;
        if (p < 1) requestAnimationFrame(step);
      })(performance.now());
    });
  });

  // ----------------------------------------------------------- typewriter
  // Types the pre-rendered content of the element. The full text is in the DOM
  // first, so a crawler or a reader with JS off sees all of it.
  document.querySelectorAll('[data-typewriter]').forEach(function (el) {
    var full = el.textContent;
    if (reduced) return;
    var speed = parseFloat(el.dataset.typewriter) || 9;
    el.textContent = '';
    el.classList.add('tw-active');
    onceVisible(el, function () {
      var i = 0;
      (function tick() {
        // Several characters per frame: one-per-frame is unbearably slow for a
        // block of terminal output.
        i = Math.min(i + speed, full.length);
        el.textContent = full.slice(0, i);
        if (i < full.length) requestAnimationFrame(tick);
        else el.classList.remove('tw-active');
      })();
    });
  });

  // -------------------------------------------------------------- marquee
  // Duplicates its children once so the CSS translate can loop seamlessly.
  document.querySelectorAll('[data-marquee]').forEach(function (el) {
    var track = el.firstElementChild;
    if (!track) return;
    track.innerHTML += track.innerHTML;
  });

  // -------------------------------------------------- scroll-pinned steps
  // A sticky visual whose active step follows the scroll position of the
  // adjacent prose. Falls back to every step simply being visible.
  document.querySelectorAll('[data-steps]').forEach(function (root) {
    var steps = Array.prototype.slice.call(root.querySelectorAll('[data-step]'));
    var panels = Array.prototype.slice.call(root.querySelectorAll('[data-step-panel]'));
    if (!steps.length || !hasIO) { root.classList.add('steps-static'); return; }

    function activate(idx) {
      steps.forEach(function (s, i) { s.classList.toggle('is-active', i === idx); });
      panels.forEach(function (p, i) { p.classList.toggle('is-active', i === idx); });
    }
    activate(0);

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) activate(steps.indexOf(e.target));
      });
    }, { rootMargin: '-45% 0px -45% 0px' });
    steps.forEach(function (s) { io.observe(s); });
  });

  // ----------------------------------------------------------------- tabs
  // Crossfades panels rather than swapping them, so switching between product
  // screenshots does not flash the page background between images.
  document.querySelectorAll('[data-tabs]').forEach(function (root) {
    var buttons = Array.prototype.slice.call(root.querySelectorAll('[data-tab]'));
    var panels = Array.prototype.slice.call(root.querySelectorAll('[data-tab-panel]'));
    if (!buttons.length) return;

    function select(idx) {
      buttons.forEach(function (b, i) {
        var on = i === idx;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-selected', on ? 'true' : 'false');
        b.tabIndex = on ? 0 : -1;
      });
      panels.forEach(function (p, i) { p.classList.toggle('is-active', i === idx); });
    }
    select(0);

    buttons.forEach(function (b, i) {
      b.addEventListener('click', function () { select(i); });
      // Arrow-key navigation is what makes this a tablist rather than a row of
      // buttons that happen to look like one.
      b.addEventListener('keydown', function (e) {
        var d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (!d) return;
        e.preventDefault();
        var next = (i + d + buttons.length) % buttons.length;
        select(next);
        buttons[next].focus();
      });
    });
  });

  // ------------------------------------------------------ generic reveals
  var revealables = document.querySelectorAll('.reveal');
  if (!hasIO || reduced) {
    revealables.forEach(function (el) { el.classList.add('in'); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
      });
    }, { rootMargin: '0px 0px -8% 0px' });
    revealables.forEach(function (el) { io.observe(el); });
  }
})();

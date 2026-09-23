/* Hero animation: the logo, alive.
 *
 * The mark is three agents orbiting one sealed record. This is the same idea at
 * hero scale -- a central node with satellites on tilted elliptical paths, so
 * the thing in the corner of the nav and the thing behind the headline are
 * recognisably the same object rather than two unrelated decorations.
 *
 * Constraints it respects, because this sits behind body copy:
 *   - low contrast, so the headline stays the most legible thing on screen
 *   - one static frame under prefers-reduced-motion, never a frozen blank
 *   - stops rendering when the tab is hidden or the hero is scrolled past
 *   - canvas 2D, not WebGL: the marketing site loads no libraries and this is
 *     not worth the first one
 */

(function () {
  'use strict';

  var canvas = document.getElementById('hero-orbit');
  if (!canvas || !canvas.getContext) return;

  var ctx = canvas.getContext('2d');
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Radii and sizes are fractions of the smaller canvas dimension so the system
  // is proportionate on a phone and on an ultrawide without separate tuning.
  var SATELLITES = [
    { orbit: 0.30, size: 0.052, speed:  0.26, phase: 0.0,  alpha: 1.00 },
    { orbit: 0.47, size: 0.040, speed: -0.17, phase: 2.2,  alpha: 0.70 },
    { orbit: 0.66, size: 0.030, speed:  0.11, phase: 4.3,  alpha: 0.46 },
    { orbit: 0.86, size: 0.022, speed: -0.07, phase: 1.1,  alpha: 0.26 }
  ];

  var TILT = 0.34;   // vertical squash — reads as a plane seen near-edge-on
  var VIOLET = '139,124,246';
  var BLUE = '110,168,254';

  var w = 0, h = 0, cx = 0, cy = 0, unit = 0, dpr = 1, frame = null;

  function resize() {
    var rect = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = rect.width; h = rect.height;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cx = w / 2;
    // Vertical placement is per-page. The default sits below centre because on
    // a page with body copy a centred core washes out the text. A page whose
    // centre is occupied by an opaque card passes a value that lets the rings
    // frame it instead.
    cy = h * (parseFloat(canvas.dataset.orbitY) || 0.76);
    // Bounded so a very wide viewport does not fling satellites off-screen.
    unit = Math.min(w * (parseFloat(canvas.dataset.orbitScale) || 0.40), h * 0.86);
  }

  function ring(radius) {
    ctx.beginPath();
    ctx.ellipse(cx, cy, radius, radius * TILT, 0, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(' + VIOLET + ',0.10)';
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function dot(x, y, r, alpha, colour, bloom) {
    // bloom defaults to a tight halo. The centre node passes a smaller one:
    // it is the largest dot, so the same multiplier produced a blob wide
    // enough to veil whatever text sat above it.
    bloom = bloom || 2.6;
    var glow = ctx.createRadialGradient(x, y, 0, x, y, r * bloom);
    glow.addColorStop(0, 'rgba(' + colour + ',' + (alpha * 0.42) + ')');
    glow.addColorStop(1, 'rgba(' + colour + ',0)');
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(x, y, r * bloom, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = 'rgba(' + colour + ',' + alpha + ')';
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }

  function draw(t) {
    ctx.clearRect(0, 0, w, h);

    SATELLITES.forEach(function (s) { ring(s.orbit * unit); });

    // Split by depth so satellites on the far side are drawn before the centre
    // node and pass behind it, which is what sells the tilt as a plane.
    var behind = [], front = [];
    SATELLITES.forEach(function (s) {
      var a = s.phase + t * s.speed;
      var x = cx + Math.cos(a) * s.orbit * unit;
      var y = cy + Math.sin(a) * s.orbit * unit * TILT;
      var depth = Math.sin(a);                 // -1 far, +1 near
      var scale = 0.78 + 0.22 * (depth + 1) / 2;
      (depth < 0 ? behind : front).push({
        x: x, y: y,
        r: s.size * unit * scale,
        alpha: s.alpha * (0.55 + 0.45 * (depth + 1) / 2)
      });
    });

    behind.forEach(function (d) { dot(d.x, d.y, d.r, d.alpha * 0.55, BLUE); });

    // Centre: the sealed record. A slow breath rather than a pulse, so it reads
    // as alive without drawing the eye off the headline.
    var breathe = 1 + Math.sin(t * 0.6) * 0.035;
    dot(cx, cy, unit * 0.062 * breathe, 0.88, VIOLET, 2.0);

    front.forEach(function (d) { dot(d.x, d.y, d.r, d.alpha, BLUE); });
  }

  var start = null;
  function loop(now) {
    if (start === null) start = now;
    draw((now - start) / 1000);
    frame = requestAnimationFrame(loop);
  }

  function stop() {
    if (frame !== null) { cancelAnimationFrame(frame); frame = null; }
  }

  function play() {
    if (frame === null && !reduced) frame = requestAnimationFrame(loop);
  }

  resize();
  if (reduced) {
    // A single representative frame. Freezing on t=0 would stack every
    // satellite at the same angle, which looks like a rendering failure.
    draw(1.9);
  } else {
    play();
  }

  var resizeTimer;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      resize();
      if (reduced) draw(1.9);
    }, 150);
  });

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stop(); else play();
  });

  // Stop once the hero has scrolled away; there is no reason to burn frames
  // animating something nobody can see.
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { e.isIntersecting ? play() : stop(); });
    }, { threshold: 0 }).observe(canvas);
  }
})();

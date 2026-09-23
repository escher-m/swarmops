/* Homepage hero: the append-only ledger, in motion.
 *
 * A chain of sealed blocks flowing along a curve through space, with a field of
 * loose particles around it -- the agent actions that have not yet been
 * committed. Every element maps to something the product does, which is the
 * only reason a 3D scene earns its place on a compliance product's homepage.
 *
 * Deliberate constraints, because this sits behind a headline:
 *   - masked and dimmed so the type is always the most legible thing on screen
 *   - one static frame under prefers-reduced-motion
 *   - pauses when the tab is hidden or the hero scrolls away
 *   - degrades to nothing if WebGL is unavailable; the page is complete without it
 */

(function () {
  'use strict';

  var canvas = document.getElementById('hero-chain');
  if (!canvas || typeof THREE === 'undefined') return;

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var wrap = canvas.parentElement;

  var VIOLET = 0x8b7cf6;
  var BLUE = 0x6ea8fe;
  var MINT = 0x3ddc97;

  var renderer, scene, camera, frame = null, clock;
  var chain, particles, links;

  function size() {
    return { w: wrap.clientWidth || 1200, h: wrap.clientHeight || 620 };
  }

  /* A soft round sprite, generated rather than loaded -- one less asset and one
     less request. */
  function glowTexture() {
    var c = document.createElement('canvas');
    c.width = c.height = 64;
    var g = c.getContext('2d');
    var grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0.0, 'rgba(255,255,255,1)');
    grad.addColorStop(0.25, 'rgba(255,255,255,0.55)');
    grad.addColorStop(1.0, 'rgba(255,255,255,0)');
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
    var t = new THREE.Texture(c);
    t.needsUpdate = true;
    return t;
  }

  /* The path the ledger runs along. Deliberately not a straight line: a chain
     that recedes and curves reads as depth, and gives the blocks something to
     bank against as they travel. */
  function ledgerCurve() {
    return new THREE.CatmullRomCurve3([
      new THREE.Vector3(-30, -10, -30),
      new THREE.Vector3(-18, -2.0, -13),
      new THREE.Vector3(-6.5, 2.6, -2),
      new THREE.Vector3(7.5, 1.4, 1),
      new THREE.Vector3(19, -3.0, -12),
      new THREE.Vector3(30, -10, -30)
    ]);
  }

  var BLOCKS = 46;
  var curve = null;
  var blockData = [];

  function build() {
    var d = size();
    curve = ledgerCurve();

    scene = new THREE.Scene();
    // Fog does the depth grading for free and hides where the chain runs out.
    scene.fog = new THREE.FogExp2(0x08090d, 0.026);

    camera = new THREE.PerspectiveCamera(46, d.w / d.h, 0.1, 200);
    camera.position.set(0, 1.6, 25);
    camera.lookAt(0, 0.4, 0);

    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    var key = new THREE.DirectionalLight(0xffffff, 0.8);
    key.position.set(8, 14, 12);
    scene.add(key);
    var rim = new THREE.PointLight(VIOLET, 1.5, 44);
    rim.position.set(-8, 4, 8);
    scene.add(rim);
    var rim2 = new THREE.PointLight(BLUE, 1.1, 40);
    rim2.position.set(12, -2, 6);
    scene.add(rim2);

    // --- the chain ------------------------------------------------------
    chain = new THREE.Group();
    scene.add(chain);

    var geo = new THREE.BoxGeometry(1, 1, 1);
    blockData = [];
    for (var i = 0; i < BLOCKS; i++) {
      var t = i / BLOCKS;
      // Most blocks are sealed and cool-toned; a few near the head carry the
      // accent, so the eye is drawn to where the ledger is still being written.
      // The accent run sits mid-curve rather than near an end, so the eye is
      // drawn into the frame instead of off the right edge.
      var head = t > 0.40 && t < 0.58;
      var mat = new THREE.MeshStandardMaterial({
        color: head ? VIOLET : (i % 7 === 0 ? BLUE : 0x5a6478),
        emissive: head ? VIOLET : (i % 7 === 0 ? BLUE : 0x141822),
        emissiveIntensity: head ? 0.85 : 0.3,
        roughness: 0.3,
        metalness: 0.65,
        transparent: true,
        opacity: 0.94
      });
      var m = new THREE.Mesh(geo, mat);
      var s = head ? 1.15 : 0.62 + (i % 3) * 0.12;
      m.scale.set(s, s, s);
      chain.add(m);
      blockData.push({ offset: t, spin: 0.2 + (i % 5) * 0.06, scale: s });
    }

    // --- the thread between them ---------------------------------------
    var pts = curve.getPoints(220);
    links = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color: VIOLET, transparent: true, opacity: 0.3 })
    );
    scene.add(links);

    // --- uncommitted actions -------------------------------------------
    var COUNT = 520;
    var pos = new Float32Array(COUNT * 3);
    var col = new Float32Array(COUNT * 3);
    var seed = [];
    var c1 = new THREE.Color(BLUE), c2 = new THREE.Color(MINT), c3 = new THREE.Color(VIOLET);
    for (var j = 0; j < COUNT; j++) {
      var at = Math.random();
      var p = curve.getPointAt(at);
      // Scattered around the chain, densest near it -- actions clustering
      // around the record they will become part of.
      var spread = 3 + Math.pow(Math.random(), 0.6) * 13;
      var ang = Math.random() * Math.PI * 2;
      pos[j * 3] = p.x + Math.cos(ang) * spread;
      pos[j * 3 + 1] = p.y + (Math.random() - 0.5) * spread * 0.85;
      pos[j * 3 + 2] = p.z + Math.sin(ang) * spread * 0.55;
      var c = Math.random() < 0.12 ? c2 : (Math.random() < 0.5 ? c1 : c3);
      col[j * 3] = c.r; col[j * 3 + 1] = c.g; col[j * 3 + 2] = c.b;
      seed.push({ phase: Math.random() * Math.PI * 2, speed: 0.12 + Math.random() * 0.4 });
    }
    var pgeo = new THREE.BufferGeometry();
    pgeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    pgeo.setAttribute('color', new THREE.BufferAttribute(col, 3));
    particles = new THREE.Points(pgeo, new THREE.PointsMaterial({
      size: 0.34,
      map: glowTexture(),
      vertexColors: true,
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      sizeAttenuation: true
    }));
    particles.userData.seed = seed;
    particles.userData.base = pos.slice();
    scene.add(particles);
  }

  function layout(t) {
    // The chain travels along its own curve, so the ledger appears to be
    // continuously appended to rather than sitting still.
    for (var i = 0; i < chain.children.length; i++) {
      var b = chain.children[i], d = blockData[i];
      var at = (d.offset + t * 0.021) % 1;
      var p = curve.getPointAt(at);
      b.position.copy(p);
      b.rotation.x = t * d.spin * 0.5 + i;
      b.rotation.y = t * d.spin + i * 0.7;
      // Fade in and out at the ends so blocks do not pop into existence.
      var edge = Math.min(at, 1 - at);
      b.material.opacity = Math.min(0.94, edge * 9);
    }

    var arr = particles.geometry.attributes.position.array;
    var base = particles.userData.base;
    var seed = particles.userData.seed;
    for (var j = 0; j < seed.length; j++) {
      var s = seed[j], k = j * 3;
      arr[k]     = base[k]     + Math.sin(t * s.speed + s.phase) * 0.7;
      arr[k + 1] = base[k + 1] + Math.cos(t * s.speed * 0.8 + s.phase) * 0.55;
      arr[k + 2] = base[k + 2] + Math.sin(t * s.speed * 0.6 + s.phase) * 0.4;
    }
    particles.geometry.attributes.position.needsUpdate = true;

    // A slow drift rather than a spin: enough to feel alive, not enough to
    // pull the eye off the headline.
    scene.rotation.y = Math.sin(t * 0.07) * 0.1;
    scene.rotation.x = Math.sin(t * 0.05) * 0.035;
    camera.position.x = Math.sin(t * 0.09) * 1.5;
    camera.lookAt(0, 0.4, 0);
  }

  function render(t) {
    layout(t);
    renderer.render(scene, camera);
  }

  function loop() {
    frame = requestAnimationFrame(loop);
    render(clock.getElapsedTime());
  }

  function stop() { if (frame !== null) { cancelAnimationFrame(frame); frame = null; } }
  function play() { if (frame === null && !reduced) { clock.start(); loop(); } }

  try {
    var d = size();
    renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(d.w, d.h, false);
    clock = new THREE.Clock();
    build();
    if (reduced) render(6.5); else play();
  } catch (e) {
    // No WebGL, or a driver that refuses. The hero is complete without this.
    canvas.style.display = 'none';
    return;
  }

  var rt;
  window.addEventListener('resize', function () {
    clearTimeout(rt);
    rt = setTimeout(function () {
      var s = size();
      camera.aspect = s.w / s.h;
      camera.updateProjectionMatrix();
      renderer.setSize(s.w, s.h, false);
      if (reduced) render(6.5);
    }, 160);
  });

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stop(); else play();
  });

  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (es) {
      es.forEach(function (e) { e.isIntersecting ? play() : stop(); });
    }, { threshold: 0 }).observe(canvas);
  }
})();

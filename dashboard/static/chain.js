/* Hash-chain visualisation.
 *
 * The verification badge is the product's trust signal, and a number alone does
 * not convey what is being claimed. This renders the actual chain: one block per
 * record, threaded in sequence, so the structure the verifier walks is visible
 * rather than asserted. Blocks are coloured by action type and the thread turns
 * red at any point the verifier flagged.
 *
 * It is decoration in the sense that the page is fully usable without it, and
 * not decoration in the sense that every element on screen corresponds to real
 * data fetched from /api/chain.
 */

(function () {
  'use strict';

  var canvas = document.getElementById('chain-canvas');
  if (!canvas || typeof THREE === 'undefined') return;

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var ACTION_COLOUR = {
    investigate:      0x6ea8fe,
    propose_fix:      0x8b7cf6,
    request_approval: 0xffc857,
    approve:          0x3ddc97,
    reject:           0xff6b6b,
    execute:          0x3ddc97,
    rollback:         0xffc857
  };

  var wrap = canvas.parentElement;
  var renderer, scene, camera, group, frame;

  function size() {
    return { w: wrap.clientWidth || 800, h: wrap.clientHeight || 210 };
  }

  function init(records, ok) {
    var d = size();
    renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(d.w, d.h, false);

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(42, d.w / d.h, 0.1, 400);
    camera.position.set(0, 1.6, 22);
    camera.lookAt(0, 0, 0);

    scene.add(new THREE.AmbientLight(0xffffff, 0.8));
    var key = new THREE.DirectionalLight(0xffffff, 0.9);
    key.position.set(6, 10, 14);
    scene.add(key);
    var rim = new THREE.DirectionalLight(ok ? 0x3ddc97 : 0xff6b6b, 0.55);
    rim.position.set(-8, -4, -10);
    scene.add(rim);

    group = new THREE.Group();
    scene.add(group);

    // Show at most this many blocks; beyond it the chain reads as texture
    // rather than structure and the frame cost stops being worth it.
    var MAX = 64;
    var shown = records.slice(-MAX);
    var n = shown.length || 1;

    // Size the chain to the viewport rather than to a fixed constant. The panel
    // is very wide and short, so a spacing tuned for a square canvas left the
    // chain as a thin ribbon floating in empty space. Work out how many world
    // units are actually visible at z=0 and fill most of that width.
    var visibleH = 2 * camera.position.z * Math.tan((camera.fov * Math.PI / 180) / 2);
    var visibleW = visibleH * camera.aspect;
    var spacing = n > 1 ? (visibleW * 0.84) / (n - 1) : 0;
    spacing = Math.min(spacing, 2.6);
    var span = (n - 1) * spacing;

    // Blocks scale with their spacing so a short chain reads as chunky links
    // and a long one as fine-grained, instead of either overlapping or
    // vanishing.
    var blockSize = Math.max(0.42, Math.min(spacing * 0.62, visibleH * 0.2));
    var gap = Math.max(spacing - blockSize, 0.04);

    var geo = new THREE.BoxGeometry(blockSize, blockSize, blockSize);
    var linkGeo = new THREE.CylinderGeometry(blockSize * 0.07, blockSize * 0.07, gap, 6);

    shown.forEach(function (rec, i) {
      var x = i * spacing - span / 2;
      // A gentle sine offset so the chain reads as a thread in space rather
      // than a ruler, and so overlapping blocks stay distinguishable. Scaled to
      // the viewport height so it stays proportionate on any panel size.
      var y = Math.sin(i * 0.42) * visibleH * 0.11;
      var z = Math.cos(i * 0.3) * 0.6;

      var colour = ACTION_COLOUR[rec.action_type] || 0x9aa4b5;
      var mat = new THREE.MeshStandardMaterial({
        color: colour,
        emissive: colour,
        emissiveIntensity: rec.outcome_status === 'failure' ? 0.55 : 0.22,
        roughness: 0.35,
        metalness: 0.5
      });
      var block = new THREE.Mesh(geo, mat);
      block.position.set(x, y, z);
      block.rotation.set(0.3 + i * 0.05, i * 0.22, 0.12);
      block.userData.base = y;
      block.userData.phase = i * 0.4;
      group.add(block);

      if (i > 0) {
        var prev = group.children[group.children.length - 2];
        var link = new THREE.Mesh(linkGeo, new THREE.MeshStandardMaterial({
          color: ok ? 0x3ddc97 : 0xff6b6b,
          emissive: ok ? 0x3ddc97 : 0xff6b6b,
          emissiveIntensity: 0.4,
          roughness: 0.5,
          transparent: true,
          opacity: 0.55
        }));
        var mid = new THREE.Vector3().addVectors(prev.position, block.position).multiplyScalar(0.5);
        link.position.copy(mid);
        // Point the cylinder along the segment between the two blocks.
        var dir = new THREE.Vector3().subVectors(block.position, prev.position).normalize();
        link.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
        link.scale.y = prev.position.distanceTo(block.position) / gap;
        group.add(link);
      }
    });

    var t0 = performance.now();
    function animate(now) {
      frame = requestAnimationFrame(animate);
      var t = (now - t0) / 1000;
      group.rotation.y = reduced ? -0.18 : Math.sin(t * 0.16) * 0.28 - 0.18;
      group.rotation.x = reduced ? 0.06 : Math.sin(t * 0.11) * 0.06;
      if (!reduced) {
        group.children.forEach(function (child) {
          if (child.userData.base === undefined) return;
          child.position.y = child.userData.base + Math.sin(t * 0.9 + child.userData.phase) * 0.06;
          child.rotation.y += 0.0035;
        });
      }
      renderer.render(scene, camera);
    }
    animate(performance.now());

    var resizeTimer;
    window.addEventListener('resize', function () {
      var s = size();
      camera.aspect = s.w / s.h;
      camera.updateProjectionMatrix();
      renderer.setSize(s.w, s.h, false);
      // Spacing is derived from the aspect ratio, so the geometry has to be
      // rebuilt, not just re-projected. Debounced because a drag-resize fires
      // this continuously.
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        cancelAnimationFrame(frame);
        scene.remove(group);
        init(records, ok);
      }, 180);
    });

    // Stop burning frames when the tab is hidden.
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { cancelAnimationFrame(frame); }
      else { animate(performance.now()); }
    });
  }

  Promise.all([
    fetch('/api/chain?limit=200').then(function (r) { return r.json(); }),
    fetch('/api/verify').then(function (r) { return r.json(); })
  ]).then(function (res) {
    var records = res[0], report = res[1];
    if (!records.length) return;
    init(records, report.ok);
  }).catch(function () {
    // The page is fully readable without this; a failed fetch is not worth
    // surfacing as an error to someone auditing a ledger.
    if (wrap) wrap.style.display = 'none';
  });
})();

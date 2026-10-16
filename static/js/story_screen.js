// Draws the 1920x1080 story screens (overview, upset watch, elimination) from /live/data.
// The lower-right 640x360 corner is always left as bare background for the switcher's video inset.
(function () {
  const W = 1920, H = 1080;

  function colorOf(seed) {
    const k = seed > 0 ? seed : 1;
    const h = (195 + ((k - 1) * 0.618034 % 1) * 220) % 360;
    const warm = h > 15 && h < 60;
    const l = warm ? 30 : (k % 2 ? 32 : 40);
    return `hsl(${Math.round(h)}, 62%, ${l}%)`;
  }

  function layout(data) {
    const boxes = [], ribbons = [], overlays = [];
    if (data.blank) return { boxes, ribbons, overlays };
    const box = (x, y, w, h, o) => boxes.push(Object.assign({ x, y, w, h, bg: '#2c2c2c', fg: '#ffffff', fs: 18, fw: 700, text: '', extra: '', xfs: '0.8em', xfg: 'inherit', pad: '0 14px', radius: '6px', justify: 'space-between' }, o));
    const pill = (x, y, w, text, h = 36, fs = 18) => box(x, y, w, h, { text, fs, justify: 'center' });
    const robot = (x, y, w, h, seed, name, o = {}) => box(x, y, w, h, Object.assign({ bg: colorOf(seed), text: name }, o));
    const tbd = (x, y, w, h, text, o = {}) => box(x, y, w, h, Object.assign({ text, bg: '#2a2a2a', fg: '#cccccc', fw: 600, fs: 17 }, o));
    const stat = (x, y, w, label, value) => box(x, y, w, 44, { text: label, extra: value, bg: '#262626', fg: '#aaaaaa', xfg: '#ffffff', xfs: '1.2em', fs: 17, fw: 600 });
    const band = (x0, y0, h0, x1, y1, h1, fill) => {
      x0 -= 6; x1 += 6;
      const xm = (x0 + x1) / 2;
      ribbons.push({ fill, d: `M ${x0} ${y0} C ${xm} ${y0} ${xm} ${y1} ${x1} ${y1} L ${x1} ${y1 + h1} C ${xm} ${y1 + h1} ${xm} ${y0 + h0} ${x0} ${y0 + h0} Z` });
    };

    pill(560, 24, 800, data.tournament_name, 52, 28);
    const finished = data.finished || [], comingUp = data.coming_up || [];

    if (data.mode === 'overview' || !data.story) {
      pill(40, 110, 400, 'Just finished');
      finished.forEach((f, i) => {
        const y = 164 + i * 96;
        robot(40, y, 400, 44, f.seed, f.name, { fs: 20, extra: f.round, radius: '6px 6px 0 0' });
        box(40, y + 44, 400, 34, { text: f.detail, fg: '#dddddd', fs: 15, fw: 600, radius: '0 0 6px 6px' });
      });
      pill(480, 110, 1400, 'Coming up');
      comingUp.forEach((c, i) => {
        const y = 164 + i * 88;
        box(480, y, 120, 66, { text: c.tag, fs: 16, justify: 'center', radius: '6px 0 0 6px' });
        robot(600, y, 280, 66, c.a.seed, c.a.name, { fs: 22, radius: '0' });
        robot(880, y, 280, 66, c.b.seed, c.b.name, { fs: 22, radius: '0 6px 6px 0' });
        tbd(1260, y, 620, 66, c.faces, { fs: 19 });
        band(1160, y, 66, 1260, y, 66, '#2c2c2c');
      });
      return { boxes, ribbons, overlays };
    }

    const s = data.story;
    const RW = Math.round(s.round_name.length * 12.5 + 70);
    box(40, 100, 300, 44, { text: s.tag, bg: '#121212', fs: 20, justify: 'center', radius: '6px 0 0 6px' });
    box(340, 100, RW, 44, { text: s.round_name, bg: '#4f4f4f', fs: 20, pad: '0 16px 0 38px', justify: 'flex-start', radius: '0 6px 6px 0' });
    overlays.push({ fill: '#121212', d: 'M 339 100 L 361 122 L 339 144 Z' });

    const n = Math.max(s.a.path.length, s.b.path.length, 1);
    const [PR, GAP, MX, MW, DX, DW] = n <= 2 ? [560, 60, 680, 400, 1340, 540] : n <= 4 ? [900, 40, 1000, 340, 1440, 440] : [1180, 28, 1240, 280, 1580, 300];
    const PL = 40, wt = c => n > 1 ? 1 + 0.5 * c / (n - 1) : 1;
    let wsum = 0;
    for (let c = 0; c < n; c++) wsum += wt(c);
    const unit = (PR - PL - (n - 1) * GAP) / wsum;
    const colW = c => unit * wt(c);
    const colX = c => { let x = PL; for (let k = 0; k < c; k++) x += colW(k) + GAP; return x; };
    pill(PL, 168, PR - PL, 'How they got here', 32, 16);
    pill(MX, 168, MW, 'Now', 32, 16);
    pill(DX, 168, DW, 'Next', 32, 16);

    [[s.a, 216, 264], [s.b, 346, 318]].forEach(([r, py, my]) => {
      let last = null;
      if (!r.path.length) tbd(PL, py + 16, PR - PL, 40, `${r.name} · first fight`, { fs: 16 });
      r.path.forEach((e, i) => {
        const c = n - r.path.length + i, x = colX(c), cw = colW(c), small = cw < 200;
        const fit = { pad: small ? '0 8px' : '0 14px' };
        const low = e.lost || e.r.startsWith('LR');
        const ry = low ? py + 32 : py, oy = low ? py : py + 40;
        robot(x, ry, cw, 40, r.seed, r.name, Object.assign({ fs: small ? 15 : 18, extra: `${e.r} · ${e.lost ? 'L' : 'W'}`, xfs: '0.75em', radius: low ? '0 0 6px 6px' : '6px 6px 0 0' }, fit));
        robot(x, oy, cw, 32, e.opp.seed, e.opp.name, Object.assign({ fs: small ? 13 : 15, fw: 600, extra: small ? '' : `seed ${e.opp.seed}`, radius: low ? '6px 6px 0 0' : '0 0 6px 6px' }, fit));
        if (last) band(last[0], last[1], 40, x, ry, 40, colorOf(r.seed));
        last = [x + cw, ry];
      });
      robot(MX, my, MW, 48, r.seed, r.name, { fs: 24, extra: `seed ${r.seed}` });
      if (last) band(last[0], last[1], 40, MX, my, 48, colorOf(r.seed));
    });

    [[s.win, 216, 264], [s.lose, 346, 318]].forEach(([d, y, from]) => {
      box(DX, y, DW, 26, { text: d[0], fs: 13, justify: 'flex-start', radius: '6px 6px 0 0' });
      tbd(DX, y + 26, DW, 48, d[1], { fs: 22, fw: 700, fg: '#ffffff', radius: '0' });
      tbd(DX, y + 74, DW, 30, d[2], { fs: 15, radius: '0 0 6px 6px' });
      band(MX + MW, from, 48, DX, y + 26, 48, '#2c2c2c');
    });

    [[s.a, 40], [s.b, 980]].forEach(([r, x]) => {
      robot(x, 470, 900, 44, r.seed, r.name, { fs: 24, extra: `seed ${r.seed}` });
      r.stats.forEach(([label, value], j) => stat(x + (j % 2) * 456, 526 + Math.floor(j / 2) * 50, 444, label, value));
    });

    pill(40, 744, 580, 'Just finished');
    finished.forEach((f, i) => {
      const y = 792 + i * 94;
      robot(40, y, 580, 40, f.seed, f.name, { fs: 20, extra: f.round, radius: '6px 6px 0 0' });
      box(40, y + 40, 580, 32, { text: f.detail, fg: '#dddddd', fs: 15, fw: 600, radius: '0 0 6px 6px' });
    });
    pill(660, 744, 580, 'Coming up');
    comingUp.forEach((c, i) => {
      const y = 792 + i * 94;
      box(660, y, 70, 40, { text: c.tag, fs: 15, justify: 'center', radius: '6px 0 0 0' });
      robot(730, y, 255, 40, c.a.seed, c.a.name, { fs: 18, radius: '0' });
      robot(985, y, 255, 40, c.b.seed, c.b.name, { fs: 18, radius: '0 6px 0 0' });
      tbd(660, y + 40, 580, 32, c.faces, { fs: 15, radius: '0 0 6px 6px' });
    });
    return { boxes, ribbons, overlays };
  }

  const SVG_NS = 'http://www.w3.org/2000/svg';

  function svgLayer(paths) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('width', W);
    svg.setAttribute('height', H);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.style.cssText = 'position:absolute;left:0;top:0';
    paths.forEach(p => {
      const path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('d', p.d);
      path.setAttribute('fill', p.fill);
      svg.appendChild(path);
    });
    return svg;
  }

  function render(stage, data) {
    const { boxes, ribbons, overlays } = layout(data);
    stage.style.background = data.background || '#00ff00';
    stage.replaceChildren();
    stage.appendChild(svgLayer(ribbons));
    boxes.forEach(b => {
      const el = document.createElement('div');
      el.className = 'sb';
      el.style.cssText = `left:${b.x}px;top:${b.y}px;width:${b.w}px;height:${b.h}px;padding:${b.pad};border-radius:${b.radius};justify-content:${b.justify};background:${b.bg};color:${b.fg};font-size:${b.fs}px;font-weight:${b.fw}`;
      const text = document.createElement('span');
      text.className = 'sb-text';
      text.textContent = b.text;
      el.appendChild(text);
      if (b.extra) {
        const extra = document.createElement('span');
        extra.className = 'sb-extra';
        extra.style.fontSize = b.xfs;
        extra.style.color = b.xfg;
        extra.textContent = b.extra;
        el.appendChild(extra);
      }
      stage.appendChild(el);
    });
    stage.appendChild(svgLayer(overlays));
  }

  function fit(stage) {
    const scale = Math.min(window.innerWidth / W, window.innerHeight / H);
    stage.style.transform = `scale(${scale})`;
  }

  window.StoryScreen = {
    start(stage, url, intervalMs) {
      fit(stage);
      window.addEventListener('resize', () => fit(stage));
      // Fade only when a different screen is pushed; plain data refreshes redraw in place.
      const FADE_MS = 150;
      stage.style.transition = `opacity ${FADE_MS}ms ease`;
      let lastJson = null, lastKey = null;
      const show = data => {
        const json = JSON.stringify(data);
        if (json === lastJson) return;
        const changed = lastKey !== null && data.key !== lastKey;
        lastJson = json;
        lastKey = data.key;
        if (!changed) return render(stage, data);
        stage.style.opacity = 0;
        setTimeout(() => { render(stage, data); stage.style.opacity = 1; }, FADE_MS);
      };
      const load = () => fetch(url, { cache: 'no-store' })
        .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
        .then(show)
        .catch(err => console.error('[story-screen] refresh failed, keeping last frame', err));
      load();
      setInterval(load, intervalMs);
    },
    layout,
  };
})();

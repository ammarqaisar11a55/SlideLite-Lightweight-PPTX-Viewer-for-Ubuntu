// Slide transitions for the slide show.
//
// ``run(oldLayer, newLayer, spec)`` animates from the old slide to the new one
// and resolves when finished.  Both layers are positioned on top of each other
// in the same box.  Transform/opacity effects use the Web Animations API;
// pattern effects (random bars, blinds, checkerboard, dissolve...) animate a
// clip-path step by step.  Unknown effects fall back to a fade.

// Evaluated lazily so the module can also be imported outside a browser (unit tests).
const reduceMotion = {
  get matches() {
    return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  },
};

const DIRS = {
  l: [-1, 0], r: [1, 0], u: [0, -1], d: [0, 1],
  lu: [-1, -1], ru: [1, -1], ld: [-1, 1], rd: [1, 1],
};

function vec(dir, fallback = 'l') {
  return DIRS[dir] || DIRS[fallback];
}

function animate(el, keyframes, duration, easing = 'ease-in-out') {
  if (!el) return Promise.resolve();
  const anim = el.animate(keyframes, { duration, easing, fill: 'forwards' });
  return anim.finished.catch(() => {});
}

export function stepped(el, duration, frame) {
  // frame(t) returns a clip-path for progress t in [0, 1].
  return new Promise((resolve) => {
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      el.style.clipPath = frame(t);
      if (t < 1) requestAnimationFrame(tick);
      else { el.style.clipPath = ''; resolve(); }
    };
    el.style.clipPath = frame(0);
    requestAnimationFrame(tick);
  });
}

export function rectsPath(rects, w, h) {
  // rects in 0..1 units -> SVG path clip in px
  if (!rects.length) return 'path("M0 0Z")';
  return `path("${rects.map(([x, y, rw, rh]) => {
    const X = x * w; const Y = y * h; const W = rw * w; const H = rh * h;
    return `M${X} ${Y}h${W}v${H}h${-W}Z`;
  }).join('')}")`;
}

export function seededOrder(n, seed = 7) {
  const order = [...Array(n).keys()];
  let s = seed;
  for (let i = n - 1; i > 0; i -= 1) {
    s = (s * 9301 + 49297) % 233280;
    const j = Math.floor((s / 233280) * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  return order;
}

const effects = {
  none: () => Promise.resolve(),
  cut: (o, n, s) => (s.thruBlk ? throughColor(o, n, s, 'black', true) : Promise.resolve()),
  fade: (o, n, s) => {
    if (s.thruBlk || s.thruWht) return throughColor(o, n, s, s.thruWht ? 'white' : 'black');
    return animate(n, [{ opacity: 0 }, { opacity: 1 }], s.dur, 'linear');
  },
  push: (o, n, s) => {
    // dir is the direction content moves: "Push from bottom" is dir="u".
    const [dx, dy] = vec(s.dir, 'u');
    const tx = dx * 100; const ty = dy * 100;
    return Promise.all([
      animate(o, [{ transform: 'translate(0,0)' }, { transform: `translate(${tx}%, ${ty}%)` }], s.dur),
      animate(n, [{ transform: `translate(${-tx}%, ${-ty}%)` }, { transform: 'translate(0,0)' }], s.dur),
    ]);
  },
  cover: (o, n, s) => {
    const [dx, dy] = vec(s.dir, 'l');
    return animate(n, [{ transform: `translate(${-dx * 100}%, ${-dy * 100}%)` }, { transform: 'translate(0,0)' }], s.dur);
  },
  uncover: (o, n, s) => {
    const [dx, dy] = vec(s.dir, 'l');
    o.style.zIndex = '2';
    return animate(o, [{ transform: 'translate(0,0)' }, { transform: `translate(${dx * 100}%, ${dy * 100}%)` }], s.dur);
  },
  wipe: (o, n, s) => {
    // dir is the direction the wipe travels (default: towards the left)
    const start = { l: 'inset(0 0 0 100%)', r: 'inset(0 100% 0 0)', u: 'inset(100% 0 0 0)', d: 'inset(0 0 100% 0)' }[s.dir || 'l'];
    return animate(n, [{ clipPath: start }, { clipPath: 'inset(0 0 0 0)' }], s.dur, 'linear');
  },
  split: (o, n, s) => {
    const vertical = (s.orient || 'horz') === 'vert';
    if (s.dir === 'in') {
      // Edges close in: visible = everything except a shrinking centre band.
      return stepped(n, s.dur, (t) => {
        const w = n.clientWidth; const h = n.clientHeight;
        const band = vertical
          ? `M${(w * t) / 2} 0H${w * (1 - t / 2)}V${h}H${(w * t) / 2}Z`
          : `M0 ${(h * t) / 2}H${w}V${h * (1 - t / 2)}H0Z`;
        return `path(evenodd, "M0 0H${w}V${h}H0Z ${band}")`;
      });
    }
    const closed = vertical ? 'inset(0 50% 0 50%)' : 'inset(50% 0 50% 0)';
    return animate(n, [{ clipPath: closed }, { clipPath: 'inset(0 0 0 0)' }], s.dur);
  },
  circle: (o, n, s) => animate(n, [{ clipPath: 'circle(0% at 50% 50%)' }, { clipPath: 'circle(71% at 50% 50%)' }], s.dur),
  diamond: (o, n, s) => animate(n, [
    { clipPath: 'polygon(50% 50%, 50% 50%, 50% 50%, 50% 50%)' },
    { clipPath: 'polygon(50% -50%, 150% 50%, 50% 150%, -50% 50%)' },
  ], s.dur),
  plus: (o, n, s) => stepped(n, s.dur, (t) => {
    const w = n.clientWidth; const h = n.clientHeight;
    const a = (t * w) / 2; const b = (t * h) / 2;
    return `path("M${w / 2 - a} 0H${w / 2 + a}V${h}H${w / 2 - a}Z M0 ${h / 2 - b}H${w}V${h / 2 + b}H0Z")`;
  }),
  zoom: (o, n, s) => (s.dir === 'out'
    ? Promise.all([animate(o, [{ transform: 'scale(1)', opacity: 1 }, { transform: 'scale(1.6)', opacity: 0 }], s.dur), animate(n, [{ opacity: 0 }, { opacity: 1 }], s.dur)])
    : animate(n, [{ transform: 'scale(0.3)', opacity: 0 }, { transform: 'scale(1)', opacity: 1 }], s.dur)),
  newsflash: (o, n, s) => animate(n, [{ transform: 'rotate(-720deg) scale(0)', opacity: 0 }, { transform: 'rotate(0) scale(1)', opacity: 1 }], s.dur),
  randomBar: (o, n, s) => {
    const count = 48;
    const order = seededOrder(count);
    const vertical = (s.dir || 'horz') === 'vert';
    return stepped(n, s.dur, (t) => {
      const shown = order.slice(0, Math.ceil(t * count)).map((i) => (vertical ? [i / count, 0, 1 / count + 0.002, 1] : [0, i / count, 1, 1 / count + 0.002]));
      return rectsPath(shown, n.clientWidth, n.clientHeight);
    });
  },
  blinds: (o, n, s) => {
    const count = 6;
    const vertical = (s.dir || 'horz') === 'vert';
    return stepped(n, s.dur, (t) => rectsPath([...Array(count).keys()].map((i) => (vertical
      ? [i / count, 0, t / count, 1] : [0, i / count, 1, t / count])), n.clientWidth, n.clientHeight));
  },
  checker: (o, n, s) => {
    const cols = 8; const rows = 6;
    const vertical = (s.dir || 'horz') === 'vert';
    return stepped(n, s.dur, (t) => {
      const rects = [];
      for (let r = 0; r < rows; r += 1) {
        for (let c = 0; c < cols; c += 1) {
          const offset = (r + c) % 2 ? 0.5 : 0;
          const p = Math.max(0, Math.min(1, t * 2 - offset));
          if (p > 0) rects.push(vertical ? [c / cols, r / rows, 1 / cols, p / rows] : [c / cols, r / rows, p / cols, 1 / rows]);
        }
      }
      return rectsPath(rects, n.clientWidth, n.clientHeight);
    });
  },
  dissolve: (o, n, s) => {
    const cols = 32; const rows = 18; const total = cols * rows;
    const order = seededOrder(total, 11);
    return stepped(n, s.dur, (t) => rectsPath(order.slice(0, Math.ceil(t * total)).map((i) => [
      (i % cols) / cols, Math.floor(i / cols) / rows, 1 / cols + 0.001, 1 / rows + 0.001,
    ]), n.clientWidth, n.clientHeight));
  },
  strips: (o, n, s) => {
    const [dx, dy] = vec(s.dir, 'rd');
    const count = 24;
    return stepped(n, s.dur, (t) => {
      const w = n.clientWidth; const h = n.clientHeight;
      // reveal diagonal bands advancing from the corner opposite to dir
      const k = t * (count + count);
      const rects = [];
      for (let i = 0; i < count; i += 1) {
        for (let j = 0; j < count; j += 1) {
          const ii = dx > 0 ? i : count - 1 - i;
          const jj = dy > 0 ? j : count - 1 - j;
          if (ii + jj < k) rects.push([i / count, j / count, 1 / count + 0.001, 1 / count + 0.001]);
        }
      }
      return rectsPath(rects, w, h);
    });
  },
  comb: (o, n, s) => {
    const count = 12;
    const vertical = (s.dir || 'horz') === 'vert';
    // Alternate teeth slide in from opposite sides.
    return stepped(n, s.dur, (t) => rectsPath([...Array(count).keys()].map((i) => {
      const fromStart = i % 2 === 0;
      return vertical
        ? [i / count, fromStart ? 0 : 1 - t, 1 / count + 0.001, t]
        : [fromStart ? 0 : 1 - t, i / count, t, 1 / count + 0.001];
    }), n.clientWidth, n.clientHeight));
  },
  wedge: (o, n, s) => stepped(n, s.dur, (t) => wedgePath(n, t, 1)),
  wheel: (o, n, s) => stepped(n, s.dur, (t) => wedgePath(n, t, Number(s.spokes) || 4)),
};

export function wedgePath(el, t, spokes) {
  const w = el.clientWidth; const h = el.clientHeight;
  const cx = w / 2; const cy = h / 2; const r = Math.hypot(w, h);
  let d = '';
  for (let k = 0; k < spokes; k += 1) {
    const base = -Math.PI / 2 + (k * 2 * Math.PI) / spokes;
    const sweep = (t * 2 * Math.PI) / spokes;
    const a0 = spokes === 1 ? base - sweep / 2 : base;
    const steps = 16;
    d += `M${cx} ${cy}`;
    for (let i = 0; i <= steps; i += 1) {
      const a = a0 + (sweep * i) / steps;
      d += `L${cx + r * Math.cos(a)} ${cy + r * Math.sin(a)}`;
    }
    d += 'Z';
  }
  return `path("${d}")`;
}

function throughColor(o, n, s, color, instant = false) {
  const veil = document.createElement('div');
  veil.style.cssText = `position:absolute;inset:0;background:${color};z-index:3;opacity:0`;
  n.parentNode.appendChild(veil);
  const half = instant ? 1 : s.dur / 2;
  n.style.opacity = '0';
  return animate(veil, [{ opacity: 0 }, { opacity: 1 }], half, 'linear')
    .then(() => { n.style.opacity = '1'; return animate(veil, [{ opacity: 1 }, { opacity: 0 }], instant ? 120 : half, 'linear'); })
    .then(() => veil.remove());
}

export function run(oldLayer, newLayer, spec) {
  if (!spec || !oldLayer || reduceMotion.matches) return Promise.resolve();
  const effect = effects[spec.type] || effects.fade;
  const s = { ...spec, dur: Math.max(1, Math.min(spec.dur ?? 500, 10000)) };
  return effect(oldLayer, newLayer, s).catch(() => {});
}

export const supported = Object.keys(effects);

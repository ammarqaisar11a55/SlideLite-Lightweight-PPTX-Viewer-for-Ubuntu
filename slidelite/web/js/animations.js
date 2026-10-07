// Build animations for the slide show (entrance, exit, emphasis, motion).
//
// The renderer embeds each slide's timeline as JSON (data-timing):
//   build.steps[]  - click groups; step.auto starts without a click
//   build.hidden[] - targets hidden until their entrance effect
// Effects use PowerPoint's own animEffect filter descriptors where present
// ("wipe(down)", "barn(inVertical)", "blinds(horizontal)"...) and otherwise
// map preset IDs to Web Animations keyframes.  Unknown effects fall back to
// a fade so a deck never gets stuck mid-build.

import { rectsPath, seededOrder, stepped, wedgePath } from './transitions.js';

// Evaluated lazily so the module can also be imported outside a browser (unit tests).
const reduceMotion = {
  get matches() {
    return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  },
};

// Fly In / Peek In / Crawl directions (presetSubtype): where the shape comes from.
const FROM = {
  1: [0, -1], 2: [1, 0], 3: [1, -1], 4: [0, 1], 6: [1, 1], 8: [-1, 0], 9: [-1, -1], 12: [-1, 1],
};

function targetsOf(slideEl, spec) {
  const shape = slideEl.querySelector(`[data-spid="${CSS.escape(String(spec.spid))}"]`);
  if (!shape) return [];
  if (!spec.para) return [shape];
  const paras = spec.para.map((i) => shape.querySelector(`p[data-para="${i}"]`)).filter(Boolean);
  return paras.length ? paras : [shape];
}

function baseTransform(el) {
  return el.dataset.baseTransform ?? (el.dataset.baseTransform = el.style.transform || '');
}

function offsets(el, slideEl, slideW, dir) {
  const r = el.getBoundingClientRect();
  const s = slideEl.getBoundingClientRect();
  const scale = s.width / slideW || 1;
  const [dx, dy] = dir;
  let x = 0;
  let y = 0;
  if (dx > 0) x = (s.right - r.left) / scale;
  if (dx < 0) x = -(r.right - s.left) / scale;
  if (dy > 0) y = (s.bottom - r.top) / scale;
  if (dy < 0) y = -(r.bottom - s.top) / scale;
  return [x, y];
}

function clipFrames(filter, el) {
  // Returns WAAPI clip-path keyframes [hidden, shown] or a stepped frame fn.
  const [name, arg = ''] = filter.replace(')', '').split('(');
  const a = arg.toLowerCase();
  switch (name) {
    case 'wipe': {
      const start = { down: 'inset(0 0 100% 0)', up: 'inset(100% 0 0 0)', left: 'inset(0 0 0 100%)', right: 'inset(0 100% 0 0)' }[a] || 'inset(0 0 100% 0)';
      return [start, 'inset(0 0 0 0)'];
    }
    case 'barn': {
      const vertical = a.includes('vertical');
      if (a.startsWith('in')) {
        return (t) => {
          const w = el.offsetWidth; const h = el.offsetHeight;
          const band = vertical ? `M${(w * t) / 2} 0H${w * (1 - t / 2)}V${h}H${(w * t) / 2}Z` : `M0 ${(h * t) / 2}H${w}V${h * (1 - t / 2)}H0Z`;
          return `path(evenodd, "M0 0H${w}V${h}H0Z ${band}")`;
        };
      }
      return [vertical ? 'inset(0 50% 0 50%)' : 'inset(50% 0 50% 0)', 'inset(0 0 0 0)'];
    }
    case 'box':
      return a === 'in'
        ? (t) => { const w = el.offsetWidth; const h = el.offsetHeight; const ix = (w / 2) * (1 - t); const iy = (h / 2) * (1 - t); return `path(evenodd, "M0 0H${w}V${h}H0Z M${ix} ${iy}H${w - ix}V${h - iy}H${ix}Z")`; }
        : ['inset(50% 50% 50% 50%)', 'inset(0 0 0 0)'];
    case 'circle':
      return ['circle(0% at 50% 50%)', 'circle(71% at 50% 50%)'];
    case 'diamond':
      return ['polygon(50% 50%, 50% 50%, 50% 50%, 50% 50%)', 'polygon(50% -50%, 150% 50%, 50% 150%, -50% 50%)'];
    case 'plus':
      return (t) => { const w = el.offsetWidth; const h = el.offsetHeight; const x = (t * w) / 2; const y = (t * h) / 2; return `path("M${w / 2 - x} 0H${w / 2 + x}V${h}H${w / 2 - x}Z M0 ${h / 2 - y}H${w}V${h / 2 + y}H0Z")`; };
    case 'blinds': {
      const vertical = a === 'vertical';
      return (t) => rectsPath([...Array(6).keys()].map((i) => (vertical ? [i / 6, 0, t / 6, 1] : [0, i / 6, 1, t / 6])), el.offsetWidth, el.offsetHeight);
    }
    case 'checkerboard': {
      const down = a === 'down';
      return (t) => {
        const rects = [];
        for (let r = 0; r < 4; r += 1) {
          for (let c = 0; c < 8; c += 1) {
            const p = Math.max(0, Math.min(1, t * 2 - ((r + c) % 2 ? 0.5 : 0)));
            if (p > 0) rects.push(down ? [c / 8, r / 4, 1 / 8, p / 4] : [c / 8, r / 4, p / 8, 1 / 4]);
          }
        }
        return rectsPath(rects, el.offsetWidth, el.offsetHeight);
      };
    }
    case 'randombar': {
      const order = seededOrder(24, 5);
      const vertical = a === 'vertical';
      return (t) => rectsPath(order.slice(0, Math.ceil(t * 24)).map((i) => (vertical ? [i / 24, 0, 1 / 24 + 0.002, 1] : [0, i / 24, 1, 1 / 24 + 0.002])), el.offsetWidth, el.offsetHeight);
    }
    case 'dissolve': {
      const order = seededOrder(16 * 9, 3);
      return (t) => rectsPath(order.slice(0, Math.ceil(t * 144)).map((i) => [(i % 16) / 16, Math.floor(i / 16) / 9, 1 / 16 + 0.002, 1 / 9 + 0.002]), el.offsetWidth, el.offsetHeight);
    }
    case 'strips':
      return (t) => {
        const n = 10; const rects = [];
        for (let i = 0; i < n; i += 1) for (let j = 0; j < n; j += 1) if (i + j < t * 2 * n) rects.push([i / n, j / n, 1 / n + 0.002, 1 / n + 0.002]);
        return rectsPath(rects, el.offsetWidth, el.offsetHeight);
      };
    case 'wedge':
      return (t) => wedgePath(el, t, 1);
    case 'wheel':
      return (t) => wedgePath(el, t, Number(a) || 1);
    case 'slide': {
      // Peek In: slides in from an edge inside its own box.
      const from = { fromtop: [0, -1], frombottom: [0, 1], fromleft: [-1, 0], fromright: [1, 0] }[a] || [0, 1];
      return { slide: from };
    }
    default:
      return null;
  }
}

function easingFor(spec) {
  if (spec.accel && spec.decel) return 'ease-in-out';
  if (spec.decel) return 'ease-out';
  if (spec.accel) return 'ease-in';
  return 'linear';
}

/** Keyframes for an effect in its "entrance" direction (hidden -> shown). */
function entranceFrames(spec, el, ctx) {
  const base = baseTransform(el);
  const t = (s) => (base ? `${s} ${base}` : s || 'none');
  switch (spec.preset) {
    case 1: return null; // Appear
    case 2: case 7: case 54: { // Fly In, Crawl In, Glide
      const [x, y] = offsets(el, ctx.slideEl, ctx.slideW, FROM[spec.sub] || FROM[4]);
      return { frames: [{ transform: t(`translate(${x}px, ${y}px)`) }, { transform: t('') }], easing: spec.preset === 2 ? 'ease-out' : 'linear' };
    }
    case 23: // Zoom
      return { frames: [{ transform: t('scale(0)') }, { transform: t('') }], easing: 'ease-out' };
    case 53: case 29: // Fade Zoom / Ease In
      return { frames: [{ transform: t('scale(0.5)'), opacity: 0 }, { transform: t(''), opacity: 1 }], easing: 'ease-out' };
    case 55: case 17: // Expand / Stretch
      return { frames: [{ transform: t('scaleX(0.1)'), opacity: 0 }, { transform: t(''), opacity: 1 }], easing: 'ease-out' };
    case 50: // Compress
      return { frames: [{ transform: t('scaleY(0.1)'), opacity: 0 }, { transform: t(''), opacity: 1 }] };
    case 42: case 37: case 30: case 52: // Ascend, Rise Up, Float, Arc Up
      return { frames: [{ transform: t('translateY(40px)'), opacity: 0 }, { transform: t(''), opacity: 1 }], easing: 'ease-out' };
    case 47: // Descend
      return { frames: [{ transform: t('translateY(-40px)'), opacity: 0 }, { transform: t(''), opacity: 1 }], easing: 'ease-out' };
    case 26: // Bounce
      return { frames: [{ transform: t('translateY(-120px)'), opacity: 0 }, { transform: t('translateY(0)'), opacity: 1, offset: 0.55 }, { transform: t('translateY(-18px)'), offset: 0.75 }, { transform: t('') }], easing: 'ease-in' };
    case 19: case 45: case 56: // Swivel, Fade Swivel, Flip
      return { frames: [{ transform: t('perspective(800px) rotateY(90deg)'), opacity: 0 }, { transform: t('perspective(800px) rotateY(0)'), opacity: 1 }] };
    case 31: case 49: case 35: // Grow & Turn, Spinner, Pinwheel
      return { frames: [{ transform: t('scale(0) rotate(-90deg)'), opacity: 0 }, { transform: t(''), opacity: 1 }], easing: 'ease-out' };
    case 15: // Spiral In
      return { frames: [{ transform: t('translate(-300px, -200px) scale(0) rotate(-360deg)') }, { transform: t('') }] };
    case 34: case 41: case 48: case 51: case 38: // Light Speed, Whip, Sling, Zip, Swish
      return { frames: [{ transform: t('translateX(-200px) skewX(30deg)'), opacity: 0 }, { transform: t(''), opacity: 1 }], easing: 'ease-out' };
    case 43: // Center Revolve
      return { frames: [{ transform: t('rotate(-180deg) scale(0.5)'), opacity: 0 }, { transform: t(''), opacity: 1 }] };
    default:
      break;
  }
  if (spec.filter) {
    const clip = clipFrames(spec.filter, el);
    if (spec.filter.startsWith('fade') || !clip) {
      return { frames: [{ opacity: 0 }, { opacity: 1 }] };
    }
    if (typeof clip === 'function') return { stepped: clip };
    if (clip.slide) {
      const [dx, dy] = clip.slide;
      return { frames: [{ transform: t(`translate(${dx * 100}%, ${dy * 100}%)`), clipPath: 'inset(0 0 0 0)' }, { transform: t('') }] };
    }
    return { frames: [{ clipPath: clip[0] }, { clipPath: clip[1] }] };
  }
  if (spec.scale) return { frames: [{ transform: t('scale(0)') }, { transform: t('') }] };
  return { frames: [{ opacity: 0 }, { opacity: 1 }] }; // graceful fallback
}

function emphasisFrames(spec, el) {
  const base = baseTransform(el);
  const t = (s) => (base ? `${s} ${base}` : s || 'none');
  switch (spec.preset) {
    case 6: { // Grow / Shrink
      const [sx, sy] = spec.scale || [1.5, 1.5];
      return { frames: [{ transform: t('') }, { transform: t(`scale(${sx}, ${sy})`) }], keep: true };
    }
    case 8: // Spin
      return { frames: [{ transform: t('') }, { transform: t(`rotate(${spec.rot ?? 360}deg)`) }], keep: (spec.rot ?? 360) % 360 !== 0 };
    case 9: // Transparency
      return { frames: [{ opacity: 1 }, { opacity: 1 - (spec.opacity ?? 0.5) }], keep: true };
    case 26: case 28: // Pulse (Flash Bulb), Grow With Color
      return { frames: [{ transform: t('') }, { transform: t('scale(1.06)'), offset: 0.5 }, { transform: t('') }] };
    case 27: case 35: // Flicker, Blink
      return { frames: [{ opacity: 1 }, { opacity: 0, offset: 0.5 }, { opacity: 1 }] };
    case 32: // Teeter
      return { frames: [{ transform: t('') }, { transform: t('rotate(4deg)'), offset: 0.25 }, { transform: t('rotate(-4deg)'), offset: 0.75 }, { transform: t('') }] };
    case 14: case 15: case 10: // Blast / Bold reveal / Bold flash
      return { frames: [{ transform: t(''), filter: 'brightness(1)' }, { transform: t('scale(1.1)'), filter: 'brightness(1.4)', offset: 0.5 }, { transform: t(''), filter: 'brightness(1)' }] };
    default:
      if (spec.scale) return emphasisFrames({ ...spec, preset: 6 }, el);
      if (spec.rot !== undefined) return emphasisFrames({ ...spec, preset: 8 }, el);
      return { frames: [{ filter: 'brightness(1)' }, { filter: 'brightness(1.25)', offset: 0.5 }, { filter: 'brightness(1)' }] };
  }
}

function motionFrames(spec, el, ctx) {
  const base = baseTransform(el);
  const pts = samplePath(spec.path || '');
  if (pts.length < 2) return null;
  const frames = pts.map(([x, y], i) => ({
    transform: `translate(${x * ctx.slideW}px, ${y * ctx.slideH}px) ${base}`.trim(),
    offset: i / (pts.length - 1),
  }));
  return { frames, keep: true };
}

export function samplePath(path) {
  // PowerPoint motion paths: "M 0 0 L 0.25 0.1 C ... E" in slide-relative units.
  const tokens = path.replace(/([MLCZEmlcze])/g, ' $1 ').trim().split(/[\s,]+/);
  const pts = [];
  let i = 0;
  let cmd = '';
  let cur = [0, 0];
  const num = () => parseFloat(tokens[i++]);
  while (i < tokens.length) {
    if (/^[A-Za-z]$/.test(tokens[i])) cmd = tokens[i++];
    if (cmd === 'Z' || cmd === 'z') {
      if (pts.length) pts.push(pts[0]);
      break;
    }
    if (cmd === 'E' || cmd === 'e') break;
    if (cmd === 'M' || cmd === 'L' || cmd === 'm' || cmd === 'l') {
      const x = num(); const y = num();
      if (Number.isNaN(x) || Number.isNaN(y)) break;
      cur = cmd === 'm' || cmd === 'l' ? [cur[0] + x, cur[1] + y] : [x, y];
      pts.push(cur);
    } else if (cmd === 'C' || cmd === 'c') {
      const c = [num(), num(), num(), num(), num(), num()];
      if (c.some(Number.isNaN)) break;
      const rel = cmd === 'c' ? cur : [0, 0];
      const p0 = cur;
      const p1 = [rel[0] + c[0], rel[1] + c[1]];
      const p2 = [rel[0] + c[2], rel[1] + c[3]];
      const p3 = [rel[0] + c[4], rel[1] + c[5]];
      for (let k = 1; k <= 12; k += 1) {
        const tt = k / 12; const u = 1 - tt;
        pts.push([
          u * u * u * p0[0] + 3 * u * u * tt * p1[0] + 3 * u * tt * tt * p2[0] + tt * tt * tt * p3[0],
          u * u * u * p0[1] + 3 * u * u * tt * p1[1] + 3 * u * tt * tt * p2[1] + tt * tt * tt * p3[1],
        ]);
      }
      cur = p3;
    } else {
      i += 1;
    }
    if (pts.length > 2000) break;
  }
  return pts;
}

function hide(el) { el.style.visibility = 'hidden'; }
function show(el) { el.style.visibility = ''; }

class SlideBuilds {
  constructor(slideEl, build, { finished = false, slideW, slideH }) {
    this.slideEl = slideEl;
    this.steps = (build && build.steps) || [];
    this.index = 0;
    this.running = [];
    this.ctx = { slideEl, slideW, slideH };
    if (!this.steps.length) return;
    if (finished) {
      this.applyFinalState();
      this.index = this.steps.length;
      return;
    }
    for (const spec of build.hidden || []) targetsOf(slideEl, spec).forEach(hide);
  }

  hasNext() {
    return this.index < this.steps.length;
  }

  async autoStart() {
    while (this.index < this.steps.length && this.steps[this.index].auto) {
      await this.runStep(this.steps[this.index++]);
    }
  }

  async step() {
    if (this.running.length) {
      // A click during an animation completes it (PowerPoint behaviour).
      this.completeRunning();
      return true;
    }
    if (!this.hasNext()) return false;
    await this.runStep(this.steps[this.index++]);
    // Groups set to start "with/after previous" at the top level follow on.
    while (this.index < this.steps.length && this.steps[this.index].auto) {
      await this.runStep(this.steps[this.index++]);
    }
    return true;
  }

  runStep(step) {
    const jobs = step.effects.map((spec) => new Promise((resolve) => {
      const timer = setTimeout(() => this.runEffect(spec).then(resolve), reduceMotion.matches ? 0 : spec.start || 0);
      this.running.push({ timer, resolve, spec });
    }));
    return Promise.all(jobs).then(() => { this.running = []; });
  }

  runEffect(spec) {
    const els = targetsOf(this.slideEl, spec);
    if (!els.length) return Promise.resolve();
    const duration = reduceMotion.matches ? 1 : Math.max(1, spec.dur || 500);
    const iterations = spec.repeat && spec.repeat > 1 ? spec.repeat : 1;
    return Promise.all(els.map((el) => {
      let plan;
      if (spec.cls === 'entr' || spec.cls === 'exit') plan = entranceFrames(spec, el, this.ctx);
      else if (spec.cls === 'emph') plan = emphasisFrames(spec, el);
      else if (spec.cls === 'path') plan = motionFrames(spec, el, this.ctx);
      const exit = spec.cls === 'exit';
      if (spec.cls === 'entr') show(el);
      if (!plan) {
        if (exit) hide(el);
        return Promise.resolve();
      }
      if (plan.stepped) {
        const frame = exit ? (t) => plan.stepped(1 - t) : plan.stepped;
        return stepped(el, duration, frame).then(() => { if (exit) hide(el); });
      }
      const frames = exit ? [...plan.frames].reverse().map((f, i, arr) => ({ ...f, offset: f.offset !== undefined ? 1 - f.offset : i / (arr.length - 1) })) : plan.frames;
      const anim = el.animate(frames, {
        duration,
        iterations,
        direction: spec.autoRev ? 'alternate' : 'normal',
        easing: plan.easing || easingFor(spec),
        fill: plan.keep ? 'forwards' : 'none',
      });
      el.slAnimations = (el.slAnimations || []).concat(anim);
      return anim.finished.catch(() => {}).then(() => { if (exit) hide(el); });
    }));
  }

  completeRunning() {
    for (const job of this.running) {
      clearTimeout(job.timer);
      const els = targetsOf(this.slideEl, job.spec);
      els.forEach((el) => {
        (el.slAnimations || []).forEach((a) => { try { a.finish(); } catch { /* finished */ } });
        el.style.clipPath = '';
        if (job.spec.cls === 'entr') show(el);
        if (job.spec.cls === 'exit') hide(el);
      });
      job.resolve();
    }
    this.running = [];
  }

  applyFinalState() {
    const last = new Map();
    for (const step of this.steps) {
      for (const spec of step.effects) {
        last.set(`${spec.spid}|${(spec.para || []).join(',')}`, spec);
      }
    }
    for (const spec of last.values()) {
      const els = targetsOf(this.slideEl, spec);
      if (spec.cls === 'exit') els.forEach(hide);
      else if (spec.cls === 'entr') els.forEach(show);
    }
  }

  finish() {
    this.completeRunning();
  }
}

export class Builds {
  constructor(doc) {
    this.doc = doc;
  }

  prepare(slideEl, timing, options = {}) {
    return new SlideBuilds(slideEl, timing.build, {
      ...options,
      slideW: this.doc ? this.doc.width : slideEl.offsetWidth,
      slideH: this.doc ? this.doc.height : slideEl.offsetHeight,
    });
  }
}

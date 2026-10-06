// Slide show: fullscreen presentation with transitions and builds.
//
// Keys: → ↓ Space PageDown N Enter = next · ← ↑ PageUp Backspace P = previous
// Home/End = first/last · number + Enter = go to slide · B/. black, W/, white
// Esc = end.  Click = next, right-click = previous, wheel = next/previous.

import { fetchSlide, mountSlide } from './slides.js';
import { run as runTransition } from './transitions.js';

const CURSOR_HIDE_MS = 2000;

/** Builds placeholder: a slide without animations has nothing to step. */
export const NoBuilds = {
  prepare() { return { hasNext: () => false, step: async () => false, finish() {}, autoStart: async () => {} }; },
};

export class Presenter {
  constructor({ root, stage, blank, hud, onStart, onStop, onLink, builds = NoBuilds }) {
    this.root = root;
    this.stage = stage;
    this.blank = blank;
    this.hud = hud;
    this.onStart = onStart || (() => {});
    this.onStop = onStop || (() => {});
    this.onLink = onLink || (() => false);
    this.builds = builds;
    this.active = false;
    this.busy = false;
    this.queue = [];
    this.layer = null;
    this.position = 0;
    this.order = [];
    this.typed = '';
    this.timers = new Set();
    this.cursorTimer = 0;
    this.ended = false;
    this.slideBuilds = null;

    root.addEventListener('click', (e) => this.onClick(e));
    root.addEventListener('contextmenu', (e) => { e.preventDefault(); if (this.active) this.prev(); });
    root.addEventListener('wheel', (e) => {
      if (!this.active) return;
      e.preventDefault();
      if (Math.abs(e.deltaY) < 4) return;
      if (e.deltaY > 0) this.next(); else this.prev();
    }, { passive: false });
    root.addEventListener('mousemove', () => this.showCursor());
    window.addEventListener('resize', () => this.layout());
    document.addEventListener('keydown', (e) => this.onKey(e), true);
  }

  get index() {
    return this.order[this.position] ?? 0;
  }

  // -- lifecycle --------------------------------------------------------------
  async start(doc, from = 0) {
    if (this.active) return;
    this.doc = doc;
    const visible = doc.slides.filter((s) => !s.hidden).map((s) => s.index);
    this.order = visible.length ? visible : doc.slides.map((s) => s.index);
    if (!this.order.includes(from)) {
      // Starting on a hidden slide shows it, then continues with visible ones.
      this.order = [...this.order, from].sort((a, b) => a - b);
    }
    this.position = Math.max(0, this.order.indexOf(from));
    this.active = true;
    this.ended = false;
    this.root.hidden = false;
    this.stage.replaceChildren();
    this.setBlank(null);
    this.onStart();
    this.root.focus({ preventScroll: true });
    this.showCursor();
    await this.show(this.position, { transition: false });
  }

  stop() {
    if (!this.active) return;
    this.active = false;
    this.clearTimers();
    if (this.slideBuilds) this.slideBuilds.finish();
    this.slideBuilds = null;
    this.root.hidden = true;
    this.stage.replaceChildren();
    this.layer = null;
    this.onStop(this.index);
  }

  // -- layout ----------------------------------------------------------------------
  fit() {
    const W = this.root.clientWidth || window.innerWidth;
    const H = this.root.clientHeight || window.innerHeight;
    const scale = Math.min(W / this.doc.width, H / this.doc.height);
    const w = this.doc.width * scale;
    const h = this.doc.height * scale;
    return { scale, left: (W - w) / 2, top: (H - h) / 2, w, h };
  }

  makeLayer() {
    const layer = document.createElement('div');
    layer.className = 'pres-layer';
    const host = document.createElement('div');
    host.className = 'slide-host';
    layer.appendChild(host);
    this.applyLayout(layer);
    return layer;
  }

  applyLayout(layer) {
    const f = this.fit();
    Object.assign(layer.style, { left: `${f.left}px`, top: `${f.top}px`, width: `${f.w}px`, height: `${f.h}px` });
    const host = layer.firstElementChild;
    Object.assign(host.style, {
      width: `${this.doc.width}px`, height: `${this.doc.height}px`,
      transform: `scale(${f.scale})`, transformOrigin: '0 0', position: 'absolute', left: '0', top: '0',
    });
  }

  layout() {
    if (!this.active) return;
    this.stage.querySelectorAll('.pres-layer').forEach((l) => this.applyLayout(l));
  }

  // -- navigation ----------------------------------------------------------------------
  async show(position, { transition = true, finished = false } = {}) {
    this.clearTimers();
    this.ended = false;
    this.setBlank(null);
    if (this.slideBuilds) this.slideBuilds.finish();
    this.position = position;
    const index = this.order[position];
    const layer = this.makeLayer();
    const slideEl = await mountSlide(layer.firstElementChild, index);
    if (!this.active) return;
    const timing = parseTiming(slideEl);
    this.slideBuilds = this.builds.prepare(slideEl, timing, { finished });
    const old = this.layer;
    this.stage.appendChild(layer);
    this.layer = layer;
    this.preload(position + 1);
    if (old && transition && timing.transition && timing.transition.type !== 'none') {
      this.busy = true;
      await runTransition(old, layer, timing.transition);
      this.busy = false;
    }
    if (old && old.parentNode) old.remove();
    // Drop any stale layers left by interrupted transitions.
    this.stage.querySelectorAll('.pres-layer').forEach((l) => { if (l !== layer) l.remove(); });
    layer.getAnimations({ subtree: false }).forEach((a) => a.cancel());
    layer.style.clipPath = '';
    layer.style.opacity = '';
    layer.style.transform = '';
    if (!finished) await this.slideBuilds.autoStart();
    this.scheduleAdvance(timing);
  }

  preload(position) {
    const index = this.order[position];
    if (index !== undefined) fetchSlide(index);
  }

  scheduleAdvance(timing) {
    const t = timing.transition;
    if (!t || t.advTm === undefined || this.ended) return;
    const go = () => {
      if (!this.active) return;
      if (this.slideBuilds && this.slideBuilds.hasNext()) {
        this.slideBuilds.step().then(() => this.scheduleAdvance(timing));
      } else {
        this.next();
      }
    };
    this.timer(go, Math.max(0, t.advTm));
  }

  async next() {
    if (!this.active) return;
    if (this.slideBuilds && this.slideBuilds.running && this.slideBuilds.running.length) {
      this.slideBuilds.step(); // completes the running animation immediately
      return;
    }
    if (this.busy) return;
    if (this.ended) { this.stop(); return; }
    if (this.slideBuilds && this.slideBuilds.hasNext()) {
      this.slideBuilds.step();
      return;
    }
    if (this.position >= this.order.length - 1) {
      this.showEnd();
      return;
    }
    await this.show(this.position + 1);
  }

  async prev() {
    if (!this.active || this.busy) return;
    if (this.ended) {
      this.ended = false;
      this.setBlank(null);
      return;
    }
    if (this.position === 0) return;
    // Going back shows the previous slide fully built, without a transition.
    await this.show(this.position - 1, { transition: false, finished: true });
  }

  async goToSlide(number) {
    const index = number - 1;
    let pos = this.order.indexOf(index);
    if (pos < 0 && index >= 0 && index < this.doc.slideCount) {
      this.order = [...new Set([...this.order, index])].sort((a, b) => a - b);
      pos = this.order.indexOf(index);
    }
    if (pos >= 0) await this.show(pos, { transition: false });
  }

  showEnd() {
    this.clearTimers();
    this.ended = true;
    this.setBlank('end');
  }

  // -- screens -------------------------------------------------------------------------------
  setBlank(kind) {
    const blank = this.blank;
    blank.className = '';
    blank.textContent = '';
    if (!kind) { blank.hidden = true; return; }
    blank.hidden = false;
    if (kind === 'end') {
      blank.classList.add('black', 'end');
      blank.textContent = 'End of slide show. Click to exit.';
    } else {
      blank.classList.add(kind);
    }
  }

  toggleBlank(kind) {
    if (this.ended) return;
    const showing = !this.blank.hidden && this.blank.classList.contains(kind);
    this.setBlank(showing ? null : kind);
  }

  showCursor() {
    this.root.classList.add('show-cursor');
    clearTimeout(this.cursorTimer);
    this.cursorTimer = setTimeout(() => this.root.classList.remove('show-cursor'), CURSOR_HIDE_MS);
  }

  hudText(text) {
    this.hud.textContent = text;
    this.hud.classList.add('visible');
    clearTimeout(this.hudTimer);
    this.hudTimer = setTimeout(() => this.hud.classList.remove('visible'), 1200);
  }

  // -- input ------------------------------------------------------------------------------------
  onClick(e) {
    if (!this.active) return;
    if (e.button !== 0) return;
    if (this.onLink(e.target)) return;
    if (!this.blank.hidden && !this.ended) { this.setBlank(null); return; }
    const timing = this.layer ? parseTiming(this.layer.querySelector('.slide')) : {};
    if (timing.transition && timing.transition.advClick === false && !(this.slideBuilds && this.slideBuilds.hasNext()) && !this.ended) return;
    this.next();
  }

  onKey(e) {
    if (!this.active) return;
    const key = e.key;
    if (/^[0-9]$/.test(key)) {
      this.typed = (this.typed + key).slice(-4);
      this.hudText(`Go to slide ${this.typed}`);
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    let handled = true;
    switch (key) {
      case 'Enter':
        if (this.typed) {
          const n = parseInt(this.typed, 10);
          this.typed = '';
          this.goToSlide(n);
        } else {
          this.next();
        }
        break;
      case 'ArrowRight': case 'ArrowDown': case ' ': case 'PageDown': case 'n': case 'N':
        if (!this.blank.hidden && !this.ended) this.setBlank(null); else this.next();
        break;
      case 'ArrowLeft': case 'ArrowUp': case 'PageUp': case 'Backspace': case 'p': case 'P':
        this.prev();
        break;
      case 'Home':
        this.show(0, { transition: false });
        break;
      case 'End':
        this.show(this.order.length - 1, { transition: false });
        break;
      case 'b': case 'B': case '.':
        this.toggleBlank('black');
        break;
      case 'w': case 'W': case ',':
        this.toggleBlank('white');
        break;
      case 'Escape': case '-':
        this.stop();
        break;
      default:
        handled = false;
    }
    if (handled) {
      this.typed = key === 'Enter' ? '' : this.typed;
      e.preventDefault();
      e.stopPropagation();
    }
  }

  // -- timers ---------------------------------------------------------------------------------------
  timer(fn, ms) {
    const id = setTimeout(() => { this.timers.delete(id); fn(); }, ms);
    this.timers.add(id);
  }

  clearTimers() {
    for (const id of this.timers) clearTimeout(id);
    this.timers.clear();
  }
}

export function parseTiming(slideEl) {
  if (!slideEl || !slideEl.dataset || !slideEl.dataset.timing) return {};
  try {
    return JSON.parse(slideEl.dataset.timing);
  } catch {
    return {};
  }
}

// Slide thumbnail sidebar.
//
// Thumbnails reuse the rendered slide markup, scaled down with a CSS
// transform.  They are mounted lazily when they come near the viewport and
// unmounted again when far away, so even 300-slide decks keep a small DOM.

import { mountSlide } from './slides.js';

const MAX_MOUNTED = 36;

export class Thumbnails {
  constructor(list, { onSelect }) {
    this.list = list;
    this.onSelect = onSelect;
    this.items = [];
    this.mounted = new Map(); // index -> last time it became visible
    this.visible = new Set();
    this.current = -1;
    this.doc = null;
    this.observer = new IntersectionObserver((entries) => this.onIntersect(entries), {
      root: list,
      rootMargin: '600px 0px',
    });
    this.resizeObserver = new ResizeObserver(() => this.layout());
    this.resizeObserver.observe(list);
    list.addEventListener('click', (e) => {
      const item = e.target.closest('.thumb');
      if (item) this.onSelect(Number(item.dataset.index));
    });
  }

  load(doc) {
    this.doc = doc;
    this.observer.disconnect();
    this.mounted.clear();
    this.visible.clear();
    this.current = -1;
    const fragment = document.createDocumentFragment();
    this.items = doc.slides.map((slide, i) => {
      const li = document.createElement('li');
      li.className = 'thumb';
      if (slide.hidden) li.classList.add('hidden-slide');
      li.id = `thumb-${i}`;
      li.dataset.index = String(i);
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', 'false');
      li.setAttribute('aria-label', `Slide ${i + 1}${slide.hidden ? ' (hidden)' : ''}`);
      li.innerHTML = `<span class="thumb-num" aria-hidden="true">${i + 1}</span>`
        + `<div class="thumb-frame" style="aspect-ratio:${doc.width} / ${doc.height}">`
        + '<div class="slide-host"></div></div>';
      fragment.appendChild(li);
      return li;
    });
    this.list.replaceChildren(fragment);
    this.layout();
    for (const item of this.items) this.observer.observe(item);
  }

  clear() {
    this.observer.disconnect();
    this.items = [];
    this.mounted.clear();
    this.list.replaceChildren();
    this.doc = null;
  }

  layout() {
    if (!this.doc || !this.items.length) return;
    const frame = this.items[0].querySelector('.thumb-frame');
    const width = frame.clientWidth;
    if (!width) return;
    this.scale = width / this.doc.width;
    for (const item of this.items) {
      const host = item.querySelector('.slide-host');
      host.style.width = `${this.doc.width}px`;
      host.style.height = `${this.doc.height}px`;
      host.style.transform = `scale(${this.scale})`;
    }
  }

  onIntersect(entries) {
    for (const entry of entries) {
      const index = Number(entry.target.dataset.index);
      if (entry.isIntersecting) {
        this.visible.add(index);
        this.mount(index);
      } else {
        this.visible.delete(index);
      }
    }
    this.evict();
  }

  async mount(index) {
    this.mounted.set(index, performance.now());
    const item = this.items[index];
    if (!item || item.dataset.mounted) return;
    item.dataset.mounted = '1';
    const host = item.querySelector('.slide-host');
    await mountSlide(host, index);
    // Thumbnails are static: media must never play here.
    host.querySelectorAll('video, audio').forEach((m) => { m.removeAttribute('autoplay'); m.preload = 'none'; });
  }

  evict() {
    if (this.mounted.size <= MAX_MOUNTED) return;
    const candidates = [...this.mounted.entries()]
      .filter(([i]) => !this.visible.has(i) && i !== this.current)
      .sort((a, b) => a[1] - b[1]);
    while (this.mounted.size > MAX_MOUNTED && candidates.length) {
      const [index] = candidates.shift();
      this.mounted.delete(index);
      const item = this.items[index];
      if (item) {
        delete item.dataset.mounted;
        item.querySelector('.slide-host').replaceChildren();
      }
    }
  }

  setCurrent(index, { scroll = true } = {}) {
    if (this.items[this.current]) this.items[this.current].setAttribute('aria-selected', 'false');
    this.current = index;
    const item = this.items[index];
    if (!item) return;
    item.setAttribute('aria-selected', 'true');
    this.list.setAttribute('aria-activedescendant', item.id);
    if (scroll) item.scrollIntoView({ block: 'nearest' });
  }

  invalidate(index) {
    const item = this.items[index];
    if (!item) return;
    delete item.dataset.mounted;
    if (this.visible.has(index)) this.mount(index);
  }
}

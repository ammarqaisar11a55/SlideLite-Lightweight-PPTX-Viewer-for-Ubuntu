// Main slide viewport: mounting the current slide and zoom.
//
// Zoom levels follow PowerPoint: 100% shows the slide at its physical size
// on a 96 dpi screen (1 pt = 4/3 px).  "fit" and "width" are modes that
// follow the window size.

import { mountSlide } from './slides.js';

export const ZOOM_STEPS = [0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 1, 1.25, 1.5, 2, 3, 4];
const PT_TO_PX = 96 / 72;
const PAD = 28;

export class Viewer {
  constructor({ stage, scroller, canvas, host, onZoomChange }) {
    this.stage = stage;
    this.scroller = scroller;
    this.canvas = canvas;
    this.host = host;
    this.onZoomChange = onZoomChange || (() => {});
    this.doc = null;
    this.mode = 'fit'; // 'fit' | 'width' | number
    this.scale = 1;
    this.token = 0;
    new ResizeObserver(() => this.layout()).observe(stage);
    stage.addEventListener('wheel', (e) => {
      if (!e.ctrlKey || !this.doc) return;
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      this.setZoom(this.zoom * factor, { x: e.clientX, y: e.clientY });
    }, { passive: false });
  }

  load(doc) {
    this.doc = doc;
    this.host.style.width = `${doc.width}px`;
    this.host.style.height = `${doc.height}px`;
    this.layout();
  }

  clear() {
    this.doc = null;
    this.host.replaceChildren();
  }

  async show(index) {
    const token = ++this.token;
    const staging = document.createElement('div');
    await mountSlide(staging, index);
    if (token !== this.token) return null; // a newer navigation won
    this.host.replaceChildren(...staging.childNodes);
    return this.host.firstElementChild;
  }

  /** Effective zoom as a PowerPoint-style percentage factor (1 = 100%). */
  get zoom() {
    return this.scale / PT_TO_PX;
  }

  fitScale(mode) {
    const w = Math.max(1, this.stage.clientWidth - PAD * 2);
    const h = Math.max(1, this.stage.clientHeight - PAD * 2);
    if (mode === 'width') return w / this.doc.width;
    return Math.min(w / this.doc.width, h / this.doc.height);
  }

  setMode(mode) {
    this.mode = mode;
    this.layout();
  }

  setZoom(zoom, anchor) {
    if (!this.doc) return;
    const clamped = Math.max(0.1, Math.min(4, zoom));
    const sc = this.scroller;
    // Keep the point under the cursor (or the centre) stable while zooming.
    const rect = sc.getBoundingClientRect();
    const ax = anchor ? anchor.x - rect.left : sc.clientWidth / 2;
    const ay = anchor ? anchor.y - rect.top : sc.clientHeight / 2;
    const fx = (sc.scrollLeft + ax) / Math.max(1, sc.scrollWidth);
    const fy = (sc.scrollTop + ay) / Math.max(1, sc.scrollHeight);
    this.mode = clamped;
    this.layout();
    sc.scrollLeft = fx * sc.scrollWidth - ax;
    sc.scrollTop = fy * sc.scrollHeight - ay;
  }

  zoomIn() {
    const z = this.zoom;
    this.setZoom(ZOOM_STEPS.find((s) => s > z + 0.005) ?? 4);
  }

  zoomOut() {
    const z = this.zoom;
    this.setZoom([...ZOOM_STEPS].reverse().find((s) => s < z - 0.005) ?? 0.1);
  }

  layout() {
    if (!this.doc) return;
    this.scale = typeof this.mode === 'number' ? this.mode * PT_TO_PX : Math.max(0.02, this.fitScale(this.mode));
    const w = this.doc.width * this.scale;
    const h = this.doc.height * this.scale;
    this.canvas.style.width = `${Math.round(w + PAD * 2)}px`;
    this.canvas.style.height = `${Math.round(h + PAD * 2)}px`;
    this.host.style.left = `${PAD}px`;
    this.host.style.top = `${PAD}px`;
    this.host.style.transform = `scale(${this.scale})`;
    this.onZoomChange(this.mode, this.zoom);
  }
}

// SlideLite viewer controller: document lifecycle, navigation, zoom and
// keyboard handling.  Open → View → Present.
import * as host from './host.js';
import * as slides from './slides.js';
import { Builds } from './animations.js';
import { Presenter } from './presenter.js';
import * as recent from './recent.js';
import { Thumbnails } from './thumbnails.js';
import { Viewer } from './viewer.js';

const $ = (sel) => document.querySelector(sel);
const app = $('#app');

const state = {
  theme: 'system',
  doc: null,
  current: 0,
  failed: null,
  settings: { showThumbnails: true, zoom: 'fit', useTimings: true, loop: false },
};

function setState(name) {
  app.dataset.state = name;
}

// ---- screen reader announcements --------------------------------------------------
let announceTimer = 0;
function announce(text) {
  clearTimeout(announceTimer);
  // Debounced so holding an arrow key does not flood the screen reader.
  announceTimer = setTimeout(() => { $('#sr-status').textContent = text; }, 150);
}

// ---- toast -------------------------------------------------------------------
let toastTimer = 0;
export function toast(message, ms = 2600) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('visible'), ms);
}

// ---- theme -------------------------------------------------------------------
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
function applyTheme() {
  const dark = state.theme === 'dark' || (state.theme === 'system' && (state.systemDark ?? darkQuery.matches));
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}
darkQuery.addEventListener('change', applyTheme);
host.on('theme', ({ theme, systemDark }) => {
  if (theme) state.theme = theme;
  if (typeof systemDark === 'boolean') state.systemDark = systemDark;
  applyTheme();
});

// ---- views ---------------------------------------------------------------------
const zoomSelect = $('#zoom-select');
const viewer = new Viewer({
  stage: $('#stage'),
  scroller: $('#stage-scroll'),
  canvas: $('#stage-canvas'),
  host: $('#slide-host'),
  onZoomChange: (mode, zoom) => {
    const custom = zoomSelect.querySelector('option[value="custom"]');
    if (typeof mode === 'string') {
      zoomSelect.value = mode;
      custom.hidden = true;
    } else {
      const preset = [...zoomSelect.options].find((o) => Math.abs(Number(o.value) - mode) < 0.001);
      if (preset) {
        zoomSelect.value = preset.value;
        custom.hidden = true;
      } else {
        custom.hidden = false;
        custom.textContent = `${Math.round(zoom * 100)}%`;
        zoomSelect.value = 'custom';
      }
    }
    $('#stage').classList.toggle('zoomed', mode !== 'fit');
  },
});
const thumbs = new Thumbnails($('#thumbs'), { onSelect: (i) => goTo(i, { focusThumbs: true }) });

// ---- navigation -----------------------------------------------------------------
async function goTo(index, { focusThumbs = false } = {}) {
  const doc = state.doc;
  if (!doc || !doc.slideCount) return;
  index = Math.max(0, Math.min(doc.slideCount - 1, index));
  state.current = index;
  thumbs.setCurrent(index);
  $('#slide-input').value = String(index + 1);
  $('#btn-prev').disabled = index === 0;
  $('#btn-next').disabled = index === doc.slideCount - 1;
  if (focusThumbs) $('#thumbs').focus({ preventScroll: true });
  const slideEl = await viewer.show(index);
  if (typeof viewer.mode === 'number' || viewer.mode === 'width') $('#stage-scroll').scrollTop = 0;
  announce(`Slide ${index + 1} of ${doc.slideCount}${doc.slides[index]?.hidden ? ', hidden' : ''}`);
  if (slideEl) slideEl.setAttribute('aria-label', `Slide ${index + 1} of ${doc.slideCount}`);
}

const next = () => goTo(state.current + 1);
const prev = () => goTo(state.current - 1);

$('#btn-next').addEventListener('click', next);
$('#btn-prev').addEventListener('click', prev);

const slideInput = $('#slide-input');
slideInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const n = parseInt(slideInput.value, 10);
    if (Number.isFinite(n)) goTo(n - 1);
    else slideInput.value = String(state.current + 1);
    slideInput.select();
    e.preventDefault();
  } else if (e.key === 'Escape') {
    slideInput.value = String(state.current + 1);
    $('#stage').focus();
  }
  e.stopPropagation();
});
slideInput.addEventListener('focus', () => slideInput.select());
slideInput.addEventListener('blur', () => { slideInput.value = String(state.current + 1); });

// ---- zoom ------------------------------------------------------------------------
zoomSelect.addEventListener('change', () => {
  const v = zoomSelect.value;
  if (v === 'fit' || v === 'width') viewer.setMode(v);
  else if (v !== 'custom') viewer.setZoom(Number(v));
  rememberZoom();
});

let zoomTimer = 0;
function rememberZoom() {
  // Remember the zoom mode for the next presentation (debounced for wheel zoom).
  clearTimeout(zoomTimer);
  zoomTimer = setTimeout(() => {
    const mode = typeof viewer.mode === 'number' ? Math.round(viewer.mode * 100) / 100 : viewer.mode;
    state.settings.zoom = mode;
    host.send('setting', { key: 'zoom', value: mode });
  }, 400);
}
$('#btn-zoom-in').addEventListener('click', () => { viewer.zoomIn(); rememberZoom(); });
$('#btn-zoom-out').addEventListener('click', () => { viewer.zoomOut(); rememberZoom(); });
$('#stage').addEventListener('wheel', (e) => { if (e.ctrlKey) rememberZoom(); });

// ---- sidebar ---------------------------------------------------------------------
function toggleSidebar(force, persist = true) {
  const view = $('#viewer');
  const hide = force === undefined ? !view.classList.contains('no-sidebar') : !force;
  view.classList.toggle('no-sidebar', hide);
  $('#btn-sidebar').setAttribute('aria-pressed', String(!hide));
  if (persist) {
    state.settings.showThumbnails = !hide;
    host.send('setting', { key: 'showThumbnails', value: !hide });
  }
}

// ---- settings & recent files ------------------------------------------------------------
host.on('settings', (settings) => {
  state.settings = { ...state.settings, ...settings };
  toggleSidebar(state.settings.showThumbnails !== false, false);
});

host.on('recent', (items) => {
  recent.render($('#recent'), $('#recent-list'), items || [], {
    onOpen: (item) => host.send('open-recent', { path: item.path }),
    onRemove: (item) => host.send('remove-recent', { path: item.path }),
  });
});
$('#recent-clear').addEventListener('click', () => host.send('clear-recent'));
$('#btn-sidebar').addEventListener('click', () => toggleSidebar());

// ---- project link ----------------------------------------------------------------------
// The host only ever opens its own hard-coded repository URL for this command.
document.querySelectorAll('.repo-link').forEach((el) => {
  el.addEventListener('click', (e) => { e.preventDefault(); host.send('open-repo'); });
});

// ---- links inside slides ------------------------------------------------------------
function handleSlideLink(target) {
  const el = target.closest('[data-href], [data-slide-jump], [data-jump]');
  if (!el) return false;
  if (el.dataset.href) host.send('open-link', { url: el.dataset.href });
  else if (el.dataset.slideJump) goTo(Number(el.dataset.slideJump));
  else if (el.dataset.jump) {
    const last = (state.doc?.slideCount || 1) - 1;
    const jumps = { nextslide: state.current + 1, previousslide: state.current - 1, firstslide: 0, lastslide: last };
    if (el.dataset.jump in jumps) goTo(jumps[el.dataset.jump]);
  }
  return true;
}
$('#slide-host').addEventListener('click', (e) => handleSlideLink(e.target));

// ---- keyboard ----------------------------------------------------------------------
function isTyping(el) {
  return el && (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
}

document.addEventListener('keydown', (e) => {
  if (app.dataset.state !== 'viewer' || $('#shortcuts-dialog').open) return;
  if (window.SlideLite.presenting()) return;
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl) {
    switch (e.key) {
      case '+': case '=': viewer.zoomIn(); rememberZoom(); break;
      case '-': case '_': viewer.zoomOut(); rememberZoom(); break;
      case '0': viewer.setMode('fit'); rememberZoom(); break;
      case 'g': case 'G': slideInput.focus(); break;
      case 'b': case 'B': toggleSidebar(); break;
      default: return;
    }
    e.preventDefault();
    return;
  }
  if (e.key === 'F6') {
    cycleFocus(e.shiftKey ? -1 : 1);
    e.preventDefault();
    return;
  }
  if (e.key === 'Escape' && state.fullscreen) {
    host.send('toggle-fullscreen');
    e.preventDefault();
    return;
  }
  if (isTyping(document.activeElement) || e.altKey) return;
  if (e.key === 'Enter' && document.activeElement === $('#thumbs')) {
    $('#stage').focus();
    e.preventDefault();
    return;
  }
  // Let focused buttons handle Space/Enter themselves.
  if ((e.key === ' ' || e.key === 'Enter') && document.activeElement?.tagName === 'BUTTON') return;
  switch (e.key) {
    case 'ArrowRight': case 'ArrowDown': case 'PageDown': case ' ': case 'n': case 'N':
      next(); break;
    case 'ArrowLeft': case 'ArrowUp': case 'PageUp': case 'Backspace': case 'p': case 'P':
      prev(); break;
    case 'Home': goTo(0); break;
    case 'End': goTo(state.doc.slideCount - 1); break;
    default: return;
  }
  e.preventDefault();
});

// ---- focus regions (F6) -------------------------------------------------------------------
function cycleFocus(direction) {
  const regions = [$('#thumbs'), $('#stage'), $('#btn-prev').disabled ? $('#btn-next') : $('#btn-prev')]
    .filter((el) => el && el.offsetParent !== null);
  if (!regions.length) return;
  const current = regions.findIndex((el) => el === document.activeElement || el.contains(document.activeElement));
  const next = regions[(current + direction + regions.length) % regions.length];
  next.focus();
}

host.on('fullscreen-changed', ({ fullscreen }) => { state.fullscreen = Boolean(fullscreen); });

// ---- opening files -------------------------------------------------------------------
$('#welcome-open').addEventListener('click', () => host.send('open-dialog'));

function installDropTarget() {
  let depth = 0;
  const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).some((t) => t === 'Files' || t === 'text/uri-list');
  document.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    depth += 1;
    document.body.classList.add('drag-over');
  });
  document.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (!depth) document.body.classList.remove('drag-over');
  });
  document.addEventListener('dragover', (e) => {
    if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; }
  });
  document.addEventListener('drop', (e) => {
    depth = 0;
    document.body.classList.remove('drag-over');
    const uris = (e.dataTransfer.getData('text/uri-list') || '')
      .split(/\r?\n/).map((s) => s.trim()).filter((s) => s && !s.startsWith('#'));
    if (uris.length) {
      e.preventDefault();
      host.send('open-uri', { uri: uris[0] });
    }
    // Otherwise WebKit turns the drop into a file:// navigation, which the
    // host intercepts and opens.
  });
}
installDropTarget();

// ---- document lifecycle ----------------------------------------------------------------
host.on('open-requested', ({ path }) => {
  setState('loading');
  $('#loading-text').textContent = `Opening ${path.split('/').pop()}…`;
});

host.on('document', async (info) => {
  state.doc = info;
  state.current = 0;
  state.failed = null;
  slides.setDocument(info.base);
  document.title = `${info.title} — SlideLite`;
  $('#slide-total').textContent = `/ ${info.slideCount}`;
  setState('viewer');
  const zoom = state.settings.zoom;
  viewer.mode = typeof zoom === 'number' ? zoom : (zoom === 'width' ? 'width' : 'fit');
  viewer.load(info);
  thumbs.load(info);
  await goTo(0);
  $('#stage').focus({ preventScroll: true });
  if (info.hasMacros) toast('This presentation contains macros. They are never run.');
  else if (info.issues && info.issues.length) toast('Some damaged content was skipped.');
  if (info.present || info.kind === 'slideshow') {
    document.dispatchEvent(new CustomEvent('slidelite:present', { detail: { from: 0 } }));
  }
});

host.on('load-error', (err) => {
  state.failed = err;
  $('#error-title').textContent = err.title || 'Unable to open this presentation';
  $('#error-text').textContent = [err.message, err.detail].filter(Boolean).join('\n\n');
  $('#error-anyway').hidden = !err.recoverable;
  setState('error');
  (err.recoverable ? $('#error-anyway') : $('#error-close')).focus();
});

$('#error-anyway').addEventListener('click', () => {
  if (state.failed) host.send('open-anyway', { path: state.failed.path });
});
$('#error-close').addEventListener('click', () => {
  state.failed = null;
  setState(state.doc ? 'viewer' : 'welcome');
});

// ---- slide show -------------------------------------------------------------------------------
function presenterLink(target) {
  const el = target.closest('[data-href], [data-slide-jump], [data-jump]');
  if (!el) return false;
  if (el.dataset.href) host.send('open-link', { url: el.dataset.href });
  else if (el.dataset.slideJump) presenter.goToSlide(Number(el.dataset.slideJump) + 1);
  else if (el.dataset.jump === 'nextslide') presenter.next();
  else if (el.dataset.jump === 'previousslide') presenter.prev();
  else if (el.dataset.jump === 'firstslide') presenter.goToSlide(1);
  else if (el.dataset.jump === 'lastslide') presenter.goToSlide(state.doc.slideCount);
  else if (el.dataset.jump === 'endshow') presenter.stop();
  return true;
}

const presenter = new Presenter({
  root: $('#presenter'),
  stage: $('#pres-stage'),
  blank: $('#pres-blank'),
  hud: $('#pres-hud'),
  onStart: () => host.send('present-start'),
  onStop: (index) => {
    host.send('present-stop');
    goTo(index);
    $('#stage').focus({ preventScroll: true });
  },
  onLink: presenterLink,
  options: () => state.settings,
  builds: { prepare: (el, timing, options) => new Builds(state.doc).prepare(el, timing, options) },
});

document.addEventListener('slidelite:present', (e) => {
  if (state.doc && state.doc.slideCount) presenter.start(state.doc, e.detail.from || 0);
});
host.on('present-stop', () => presenter.stop());

host.on('present', ({ from }) => {
  if (!state.doc) return;
  document.dispatchEvent(new CustomEvent('slidelite:present', { detail: { from: from === 'current' ? state.current : 0 } }));
});
$('#btn-present').addEventListener('click', () => {
  document.dispatchEvent(new CustomEvent('slidelite:present', { detail: { from: state.current } }));
});

// ---- shortcuts dialog ----------------------------------------------------------------------
const shortcuts = $('#shortcuts-dialog');
$('#shortcuts-close').addEventListener('click', () => shortcuts.close());
host.on('show-shortcuts', () => { if (!shortcuts.open) shortcuts.showModal(); });

// Introspection hook used by the GUI test-suite and developer tools.
window.SlideLite = {
  state,
  viewer,
  thumbs,
  goTo,
  showSlide: (n) => goTo(n),
  toast,
  presenter,
  presenting: () => presenter.active,
};

applyTheme();
host.send('ready');

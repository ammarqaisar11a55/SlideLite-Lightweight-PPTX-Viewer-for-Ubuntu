// SlideLite viewer controller.
import * as host from './host.js';
import * as slides from './slides.js';
import { Thumbnails } from './thumbnails.js';

const $ = (sel) => document.querySelector(sel);
const app = $('#app');

const state = {
  theme: 'system',
};

function setState(name) {
  app.dataset.state = name;
}

const thumbs = new Thumbnails($('#thumbs'), { onSelect: (i) => showSlide(i) });

// ---- theme ---------------------------------------------------------------
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

// ---- opening files ---------------------------------------------------------
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
    // Otherwise let WebKit turn the drop into a file:// navigation, which the
    // host intercepts and opens.
  });
}
installDropTarget();

host.on('open-requested', ({ path }) => {
  setState('loading');
  $('#loading-text').textContent = `Opening ${path.split('/').pop()}…`;
});

// ---- document lifecycle ------------------------------------------------------
host.on('document', async (info) => {
  state.doc = info;
  state.current = 0;
  slides.setDocument(info.base);
  document.title = `${info.title} — SlideLite`;
  setState('viewer');
  thumbs.load(info);
  await showSlide(0);
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

async function showSlide(index) {
  const info = state.doc;
  if (!info) return;
  index = Math.max(0, Math.min(info.slideCount - 1, index));
  state.current = index;
  thumbs.setCurrent(index);
  const hostEl = $('#slide-host');
  await slides.mountSlide(hostEl, index);
  hostEl.style.width = `${info.width}px`;
  hostEl.style.height = `${info.height}px`;
  $('#slide-input').value = String(index + 1);
  $('#slide-total').textContent = `/ ${info.slideCount}`;
  fit();
}

function fit() {
  const info = state.doc;
  if (!info) return;
  const stage = $('#stage');
  const pad = 32;
  const scale = Math.max(0.05, Math.min((stage.clientWidth - pad * 2) / info.width, (stage.clientHeight - pad * 2) / info.height));
  const canvas = $('#stage-canvas');
  canvas.style.width = `${info.width * scale + pad * 2}px`;
  canvas.style.height = `${info.height * scale + pad * 2}px`;
  const hostEl = $('#slide-host');
  hostEl.style.left = `${pad}px`;
  hostEl.style.top = `${pad}px`;
  hostEl.style.transform = `scale(${scale})`;
}
window.addEventListener('resize', fit);

// ---- shortcuts dialog --------------------------------------------------------
const shortcuts = $('#shortcuts-dialog');
$('#shortcuts-close').addEventListener('click', () => shortcuts.close());
host.on('show-shortcuts', () => { if (!shortcuts.open) shortcuts.showModal(); });

// Introspection hook used by the GUI test-suite and tools/screenshot.py.
window.SlideLite = { state, showSlide: (n) => showSlide(n), thumbs };

applyTheme();
host.send('ready');

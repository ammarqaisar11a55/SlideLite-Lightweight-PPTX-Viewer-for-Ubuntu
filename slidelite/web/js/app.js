// SlideLite viewer controller.
import * as host from './host.js';

const $ = (sel) => document.querySelector(sel);
const app = $('#app');

const state = {
  theme: 'system',
};

function setState(name) {
  app.dataset.state = name;
}

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

// ---- shortcuts dialog --------------------------------------------------------
const shortcuts = $('#shortcuts-dialog');
$('#shortcuts-close').addEventListener('click', () => shortcuts.close());
host.on('show-shortcuts', () => { if (!shortcuts.open) shortcuts.showModal(); });

applyTheme();
host.send('ready');

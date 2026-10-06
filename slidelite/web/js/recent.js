// Recent presentations on the welcome screen.

export function relativeTime(seconds, now = Date.now() / 1000) {
  const diff = Math.max(0, now - seconds);
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  if (diff < 2 * 86400) return 'Yesterday';
  const date = new Date(seconds * 1000);
  const sameYear = date.getFullYear() === new Date(now * 1000).getFullYear();
  return date.toLocaleDateString(undefined, sameYear ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' });
}

export function render(container, list, items, { onOpen, onRemove }) {
  container.hidden = !items.length;
  const fragment = document.createDocumentFragment();
  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'recent-item';
    if (!item.exists) li.classList.add('missing');

    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'recent-open';
    open.title = item.exists ? item.path : `${item.path} (file not found)`;
    const name = document.createElement('span');
    name.className = 'recent-name';
    name.textContent = item.name;
    const time = document.createElement('span');
    time.className = 'recent-time';
    time.textContent = relativeTime(item.opened);
    const folder = document.createElement('span');
    folder.className = 'recent-path';
    // RTL direction truncates long paths at the start; LRM marks keep the
    // path itself in left-to-right order.
    folder.textContent = `\u200E${item.folder}\u200E`;
    open.append(name, time, folder);
    open.addEventListener('click', () => onOpen(item));

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'recent-remove';
    remove.title = 'Remove from recent';
    remove.setAttribute('aria-label', `Remove ${item.name} from recent presentations`);
    remove.textContent = '×';
    remove.addEventListener('click', () => onRemove(item));

    li.append(open, remove);
    fragment.appendChild(li);
  }
  list.replaceChildren(fragment);
}

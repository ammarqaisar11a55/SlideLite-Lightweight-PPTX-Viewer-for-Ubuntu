// Fetching and mounting rendered slide markup.
//
// Slide HTML references SVG definitions through ids prefixed with "@@".  The
// same slide is mounted several times (thumbnails, viewer, slide show), so
// each copy gets its own prefix to keep ids unique in the document.

const cache = new Map();
let mountCounter = 0;
let base = '';

export function setDocument(docBase) {
  base = docBase;
  cache.clear();
}

export function fetchSlide(index) {
  const key = `${base}|${index}`;
  if (!cache.has(key)) {
    const promise = fetch(`${base}slide/${index + 1}.html`)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .catch((err) => {
        cache.delete(key);
        return `<div class="slide slide-error"><div class="slide-error-msg"><strong>This slide could not be displayed</strong><span>${String(err.message || err)}</span></div></div>`;
      });
    cache.set(key, promise);
    // Keep the markup cache bounded for very large decks.
    if (cache.size > 80) cache.delete(cache.keys().next().value);
  }
  return cache.get(key);
}

export async function mountSlide(hostEl, index) {
  const html = await fetchSlide(index);
  mountCounter += 1;
  hostEl.innerHTML = html.split('@@').join(`m${mountCounter}-`);
  return hostEl.firstElementChild;
}

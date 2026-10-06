# SlideLite Architecture

SlideLite is a **viewer only**. It opens `.pptx` files read-only, renders them as
faithfully as practical, and presents them. It never writes to the source file and
has no editing code paths.

## 1. Technology evaluation

The decision was driven by the priorities in the brief: rendering fidelity,
reliability, performance and memory, startup speed, offline operation,
maintainability, and Ubuntu compatibility.

| Approach | Fidelity | Startup / RAM | Transitions, animations, media | Verdict |
|---|---|---|---|---|
| **LibreOffice headless** (convert to PDF/PNG, show images) | Good for static content, but text and SmartArt often drift. It is the reference most Linux users know. | Poor. It is a ~300 MB suite (exactly what users want to avoid), and conversion takes 2–10 s per deck. | Lost. PDF/PNG output has no transitions, animations, video or audio. | Rejected as the engine. |
| **Electron** + JS PPTX parser | Depends on the parser. | Poor. Bundles Chromium (~150–250 MB on disk, ~200 MB RAM). | Full web platform. | Rejected: too heavy for a viewer. |
| **Tauri** (system WebKitGTK + Rust) | Depends on the parser. | Good. | Full web platform. | Viable, but needs a Rust + JS toolchain and its own OOXML parser in Rust. |
| **Qt (QML/Widgets) + custom painter** | Depends on the parser. Good text shaping. | Good. Needs Qt 6 (~80 MB if not installed). | Must hand-write a timeline engine; QtMultimedia for media. | Viable, but much more code for text layout, effects and animation. |
| **GTK + Cairo/Pango custom painter** | Good text shaping, but CSS-like box layout, bullets and autofit must be hand-built. | Excellent. | Everything (transitions, animations, video overlays) hand-written. | Highest effort for a given fidelity. |
| **GTK + system WebKitGTK, OOXML → HTML/SVG** (chosen) | High. DrawingML maps naturally onto SVG (geometry, gradients, patterns, clipping, filters) and HTML/CSS (rich text, bullets, tables). | Good. WebKitGTK and PyGObject ship with Ubuntu desktop, so there is nothing extra to download. Cold start is about 1 s. | Web Animations API for transitions and effects, HTML5 `<video>`/`<audio>` through GStreamer. | **Selected.** |

Existing open-source PPTX renderers were also considered. The JS
"pptx-to-html" projects are unmaintained and cover a small subset of DrawingML
(no theme inheritance, no preset geometry engine). python-pptx is an
authoring/reading library and does not render. None was a reliable base, so
SlideLite has its own parser. It uses the ECMA-376 preset shape definitions, so
the 187 PowerPoint auto-shapes are drawn from their exact formulas rather than
approximations.

### Why this beats "just use LibreOffice"

* The user does not need an office suite installed. That is the point of the
  product.
* Slides render in milliseconds on demand, so 300-slide decks open immediately.
* Transitions, build animations, video and audio keep working, which a
  PDF/PNG pipeline cannot do.

## 2. Runtime overview

```
┌──────────────────────── GTK 3 process (Python) ───────────────────────┐
│ Gtk.Application (single instance, D-Bus activatable)                   │
│  └─ ViewerWindow (native HeaderBar: Open · Present · menu)             │
│      └─ SlideWebView (WebKitGTK 4.1, ephemeral context)                │
│           ▲ script messages            │ slidelite:// requests          │
│           │ (JSON commands)            ▼                                │
│  Host bridge ◄────────────────► Router ──► DocumentSession              │
│                                              ├─ opc.Package (zip, rels) │
│                                              ├─ Presentation model      │
│                                              │   (themes, masters,      │
│                                              │    layouts, slides)      │
│                                              ├─ SlideRenderer → HTML/SVG│
│                                              └─ LRU render cache        │
└────────────────────────────────────────────────────────────────────────┘
          WebKit web process: viewer UI (HTML/CSS/ES modules)
          thumbnails · zoom · navigation · slide show · transitions · animations
```

* **Python host** (`slidelite/app`) owns the window, native dialogs, fullscreen,
  file associations, settings and recent files.
* **Router** (`slidelite/server/router.py`) serves everything from memory on
  the private `slidelite://app/` scheme. Examples are the UI assets,
  `doc/<id>/info.json`, `doc/<id>/slide/<n>.html` and
  `doc/<id>/part/<media part>`. The same router powers a loopback-only
  development server (`python -m slidelite.devserver`), so the UI can be
  inspected in any browser.
* **Presentation engine** (`slidelite/presentation`) is pure Python with no
  GUI imports, which makes it fully unit-testable. It parses OPC/OOXML with a
  hardened XML parser and resolves inheritance: slide → layout → master →
  theme, placeholders, list styles, colour maps and style matrices.
* **Renderer** (`slidelite/render`) turns the resolved model into a
  self-contained HTML fragment per slide. The fragment uses absolutely
  positioned shapes, SVG geometry and fills, HTML text, tables, SVG charts,
  and media elements. The coordinate system is 1 CSS px = 1 pt
  (12 700 EMU), so font sizes map 1:1 and the UI scales whole slides with
  a CSS transform. Line breaks therefore never change with zoom.
* **Web UI** (`slidelite/web`) consists of ES modules: the host bridge,
  viewer, thumbnails (lazy, IntersectionObserver, mounted only near the
  viewport), zoom, keyboard, slide show, transitions and animations (Web
  Animations API).

## 3. Security model

PPTX files are untrusted input.

* **Zip handling** (`opc.py`): it enforces limits on entry count, total
  uncompressed size and compression ratio (zip bombs). Part names are
  normalised and anything escaping the package root is rejected (path
  traversal). Nothing is extracted to disk.
* **XML** (`xmlsafe.py`): documents containing `<!DOCTYPE`/`<!ENTITY` are
  refused before parsing, which rules out XXE and entity expansion. Size
  limits apply per part.
* **Macros**: VBA projects (`vbaProject.bin`), OLE payloads and ActiveX
  controls are never executed. Only their preview images are shown.
* **Web content**: all slide text is escaped. No markup from the file
  reaches the DOM unescaped. Embedded SVG images are loaded through `<img>`,
  where scripts are inert. A strict CSP (`default-src 'none'`) and a WebKit
  content filter block every non-`slidelite:` load, so external images,
  fonts and media links are never fetched.
* **Navigation**: the view can't navigate away from the app page.
  Hyperlinks open only after the user confirms. New windows and plugins are
  disabled.
* **No disk writes**: the web context is ephemeral (no cookies, caches or
  local storage on disk). The only files SlideLite writes are its own
  settings and recent-files list under `$XDG_CONFIG_HOME/slidelite/`, plus a
  compiled content-filter under `$XDG_CACHE_HOME/slidelite/`.
* **Privacy**: no telemetry, no crash upload, no network access at all.

## 4. Module map

```
slidelite/
├── cli.py                 command line (slidelite [--fullscreen|--presentation] FILE)
├── app/                   GTK shell: application, window, hardened WebKit view
├── server/                router + document session (HTTP-free, in-memory)
├── presentation/          OOXML engine
│   ├── opc.py             zip package, content types, relationships
│   ├── xmlsafe.py         hardened XML parsing, namespaces
│   ├── units.py, color.py theme colours, colour transforms
│   ├── theme.py           colour/font/format schemes
│   ├── geometry.py        DrawingML shape-guide engine + 187 presets
│   ├── document.py        presentation, masters, layouts, slides
│   ├── shapes.py          shape tree model
│   ├── text.py            text body + style inheritance
│   ├── timing.py          transitions & animation timelines → JSON
│   └── metafile.py        EMF/WMF → SVG conversion
├── render/                model → HTML/SVG (shapes, text, tables, charts, media)
├── web/                   viewer UI (HTML/CSS/ES modules, icons)
├── settings.py            preferences (theme, zoom, slide show options)
├── recent.py              recent files
└── devserver.py           127.0.0.1 development server
```

## 5. Performance strategy

* Opening parses only `presentation.xml`, the themes, masters and layouts,
  plus a slide index. Slides are parsed and rendered on first request, then
  kept in an LRU cache.
* Thumbnails reuse the slide HTML at a small scale and are mounted lazily.
  Off-screen thumbnails are unmounted, so a 300-slide deck keeps a bounded
  DOM. Large bitmaps are served downscaled to thumbnails.
* The slide show preloads the next slide so transitions never wait.
* Media are streamed from the zip on request and never fully copied.

## 6. Known trade-offs

* Text layout comes from WebKit, not PowerPoint. Line breaks can differ
  slightly when the original fonts are missing. SlideLite substitutes
  metric-compatible fonts (Carlito for Calibri, Caladea for Cambria,
  Liberation for Arial/Times/Courier) when installed.
* 3D effects (bevels, extrusion), artistic image effects and some
  rarely-used transitions are approximated or ignored. See the README
  limitations section.
* Video playback depends on the GStreamer codecs installed. Without
  `gstreamer1.0-libav`, H.264 video shows its poster frame and a notice.

# SlideLite Architecture

SlideLite is a **viewer only**. It opens `.pptx` files read-only, renders them as
faithfully as practical, and presents them. It never writes to the source file and
has no editing code paths.

## 1. Technology evaluation

The decision was driven by the priorities in the brief: rendering fidelity,
reliability, performance and memory, startup speed, offline operation,
maintainability, and Ubuntu compatibility.

| Approach | Fidelity | Startup / RAM | Transitions and animations | Verdict |
|---|---|---|---|---|
| **LibreOffice headless** (convert to PDF/PNG, show images) | Good for static content, but text and SmartArt often drift. It is the reference most Linux users know. | Poor. It is a ~300 MB suite (exactly what users want to avoid), and conversion takes 2–10 s per deck. | Lost. PDF/PNG output has no transitions, animations, video or audio. | Rejected as the engine. |
| **Electron** + JS PPTX parser | Depends on the parser. | Poor. Bundles Chromium (~150–250 MB on disk, ~200 MB RAM). | Full web platform. | Rejected: too heavy for a viewer. |
| **Tauri** (system WebKitGTK + Rust) | Depends on the parser. | Good. | Full web platform. | Viable, but needs a Rust + JS toolchain and its own OOXML parser in Rust. |
| **Qt (QML/Widgets) + custom painter** | Depends on the parser. Good text shaping. | Good. Needs Qt 6 (~80 MB if not installed). | Must hand-write a timeline engine; QtMultimedia for media. | Viable, but much more code for text layout, effects and animation. |
| **GTK + Cairo/Pango custom painter** | Good text shaping, but CSS-like box layout, bullets and autofit must be hand-built. | Excellent. | Everything (transitions, animations, video overlays) hand-written. | Highest effort for a given fidelity. |
| **GTK + system WebKitGTK, OOXML → HTML/SVG** (chosen) | High. DrawingML maps naturally onto SVG (geometry, gradients, patterns, clipping, filters) and HTML/CSS (rich text, bullets, tables). | Good. WebKitGTK and PyGObject ship with Ubuntu desktop, so there is nothing extra to download. Cold start is about 1 s. | Web Animations API and CSS clip paths for transitions and build effects. | **Selected.** |

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
* Transitions and build animations keep working, which a PDF/PNG pipeline
  cannot do.

## 2. Runtime overview

```
┌──────────────────────── GTK 3 process (Python) ───────────────────────┐
│ Gtk.Application (single instance; one window per presentation)         │
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
  positioned shapes, SVG geometry and fills, HTML text, tables, SVG charts
  and SmartArt drawings, plus the slide's transition/animation timeline as a
  JSON data attribute. The coordinate system is 1 CSS px = 1 pt
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
  reaches the DOM unescaped. Embedded SVG pictures are loaded as images
  (SVG `<image>`), where scripts are inert. A strict CSP (`default-src 'none'`) and a WebKit
  content filter block every non-`slidelite:` load, so external images,
  fonts and media links are never fetched.
* **Navigation**: the view can't navigate away from the app page.
  Hyperlinks open only after the user confirms. New windows and plugins are
  disabled.
* **No disk writes**: the web context is ephemeral (no cookies, caches or
  local storage on disk). The only files SlideLite writes are its own
  settings (`$XDG_CONFIG_HOME/slidelite/settings.json`), the recent-files list
  (`$XDG_STATE_HOME/slidelite/recent.json`), both 0600, and a compiled
  content filter under `$XDG_CACHE_HOME/slidelite/`.
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
│   ├── document.py        presentation.xml, slide discovery, slide size
│   ├── parts.py           masters, layouts, slides, placeholder inheritance
│   ├── theme.py           colour/font/format schemes
│   ├── color.py           scheme/system/preset colours, colour transforms
│   ├── fonts.py           font substitution, symbol-font translation
│   ├── geometry.py        DrawingML shape-guide engine + 187 presets
│   ├── tablestyles.py     the 74 built-in table styles
│   ├── timing.py          transitions & animation timelines → JSON
│   └── metafile.py        EMF/WMF → SVG conversion
├── render/                model → HTML/SVG
│   ├── slide.py           background, master/layout/slide layering
│   ├── shapes.py          sp, cxnSp, pic, grpSp, graphicFrame
│   ├── text.py            text style inheritance and HTML text
│   ├── paint.py           fills, lines, arrowheads, effects
│   ├── table.py, chart.py, diagram.py
│   └── media.py           media shapes (poster frame only)
├── web/                   viewer UI (ES modules): viewer, thumbnails, presenter,
│                          transitions, animations, recent files
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
* Slide rendering and image conversion run on a small worker pool; WebKit
  requests are completed on the GTK main loop, which stays responsive.
* Measured on the development machine: the UI is ready in ~0.65 s, a
  300-slide deck opens in well under 2 s, slides render in a few
  milliseconds (worst seen: ~60 ms), and the whole app (GTK host plus
  WebKit processes) uses about 250 MB PSS.

## 6. Testing

* **Unit tests** for the engine: geometry formulas, colour transforms, fonts,
  text inheritance, tables, charts, metafiles, timing.
* **Rendering regression tests**: rendered markup of the synthetic fixtures is
  compared against reviewed snapshots in `tests/golden/`.
* **Crash tests**: deterministic fuzzing of fixtures (XML corruption, byte
  flips, truncation) and of metafiles; every outcome must be a friendly
  error or a rendered slide.
* **GUI tests** drive the real WebKit view (navigation, zoom, thumbnails,
  slide show, transitions, animations, preferences, accessibility) through
  `tests/harness.py`.
* **Stress tests**: a 300-slide deck and large images.
* **Packaging tests**: build the `.deb` and check contents, modes and the
  installed launcher.
* During development every slide of 253 real-world decks (6,201 slides) was
  rendered with no failures; those decks are private and not part of the
  repository.

## 7. Known trade-offs

* Text layout comes from WebKit, not PowerPoint. Line breaks can differ
  slightly when the original fonts are missing. SlideLite substitutes
  metric-compatible fonts (Carlito for Calibri, Caladea for Cambria,
  Liberation for Arial/Times/Courier) when installed.
* 3D effects (bevels, extrusion), artistic image effects and some
  rarely-used transitions are approximated or ignored. See the README
  limitations section.
* Audio and video are not played; media shapes show their poster frame.
  WebKitGTK's GStreamer pipeline does not read media from custom URI
  schemes, and playback was deprioritised for the first release.

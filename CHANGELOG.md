# Changelog

## Unreleased

### Added
- Opens PowerPoint 97–2003 `.ppt`, `.pps` and `.pot` files with a built-in converter:
  no LibreOffice or other software needed, and nothing is written to disk. Text,
  masters, shapes, pictures, gradients, numbered lists, transitions and click animations
  all carry over.
- Snap package, built and smoke-tested by CI on amd64 and arm64.

### Fixed
- A large first character (often a space) no longer stretches every line of a paragraph.
- Monotype Sorts and Zapf Dingbats bullets show the right symbols instead of letters.
- Tahoma text no longer runs long: it now falls back to a font with matching widths.

## 1.0.0 — 2026-10-07

First release.

### Viewing
- Opens `.pptx`, `.ppsx`, `.pptm` and `.potx`; explains legacy `.ppt`, encrypted, empty,
  damaged and Office lock files, with *Open Anyway* for recoverable damage.
- Renders themes, masters and layouts, rich text, all 187 preset shapes and custom
  geometry, fills, lines, effects, pictures, tables (74 built-in styles), charts,
  SmartArt and EMF/WMF metafiles.
- Thumbnail sidebar, zoom (fit, width, presets, custom), keyboard navigation, go to slide.
- Light, dark and system themes; recent presentations; remembered zoom and sidebar.

### Presenting
- Fullscreen slide show with transitions and build animations, auto-advance timings,
  loop mode, black/white screens and an end-of-show screen.

### Platform
- Offline only, no telemetry; hardened against malicious files.
- `.deb` package with desktop entry, file associations, icons, AppStream metadata and a
  man page.

### Known limitations
- Audio and video show their poster frame and are not played.
- See the README for the full list.

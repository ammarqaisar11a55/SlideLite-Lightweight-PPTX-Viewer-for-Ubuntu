#!/usr/bin/env bash
# Build slidelite_<version>_all.deb without root.
#
#   packaging/build-deb.sh [output-dir]
#
# Staging happens in a temporary directory on a POSIX filesystem so file
# modes are exact (the source tree may live on NTFS/FAT where they are not).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$ROOT}"
VERSION="$(cd "$ROOT" && python3 -c 'import slidelite; print(slidelite.__version__)')"
APP_ID="io.github.ammarqaisar.SlideLite"
PKG="slidelite_${VERSION}_all"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
DEST="$STAGE/$PKG"

mkdir -p "$DEST/DEBIAN" "$DEST/usr/bin" "$DEST/usr/share/slidelite" \
  "$DEST/usr/share/applications" "$DEST/usr/share/metainfo" \
  "$DEST/usr/share/icons/hicolor/scalable/apps" "$DEST/usr/share/man/man1" \
  "$DEST/usr/share/doc/slidelite"

# Application code (no caches, no tests).
(cd "$ROOT" && tar --exclude='__pycache__' --exclude='*.pyc' -cf - slidelite) | tar -xf - -C "$DEST/usr/share/slidelite"

cat > "$DEST/usr/bin/slidelite" <<'LAUNCHER'
#!/usr/bin/python3
import sys

sys.path.insert(0, "/usr/share/slidelite")
sys.dont_write_bytecode = True

from slidelite.cli import main  # noqa: E402

sys.exit(main())
LAUNCHER

cp "$ROOT/packaging/$APP_ID.desktop" "$DEST/usr/share/applications/"
cp "$ROOT/packaging/$APP_ID.metainfo.xml" "$DEST/usr/share/metainfo/"
cp "$ROOT/slidelite/web/icons/slidelite.svg" "$DEST/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"
gzip -9n -c "$ROOT/packaging/slidelite.1" > "$DEST/usr/share/man/man1/slidelite.1.gz"

# Raster icons for menus/docks that prefer PNG (needs GdkPixbuf + librsvg).
python3 - "$ROOT/slidelite/web/icons/slidelite.svg" "$DEST/usr/share/icons/hicolor" "$APP_ID" <<'ICONS' || echo "note: PNG icons skipped (GdkPixbuf/librsvg unavailable)"
import os, sys
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
svg, base, app_id = sys.argv[1:4]
for size in (16, 24, 32, 48, 64, 128, 256, 512):
    target = os.path.join(base, f"{size}x{size}", "apps")
    os.makedirs(target, exist_ok=True)
    GdkPixbuf.Pixbuf.new_from_file_at_size(svg, size, size).savev(os.path.join(target, f"{app_id}.png"), "png", [], [])
ICONS

cat > "$DEST/usr/share/doc/slidelite/copyright" <<COPYRIGHT
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: SlideLite
Source: https://github.com/ammarqaisar11a55/SlideLite-Lightweight-PPTX-Viewer-for-Ubuntu

Files: *
Copyright: 2026 Muhammad Ammar Qaisar
License: MIT

Files: slidelite/data/presets.json
Copyright: Ecma International
License: ECMA-376
 Preset shape geometry derived from ECMA-376 Part 1, Annex D
 (presetShapeDefinitions.xml), freely reproducible per the Ecma copyright notice.

License: MIT
$(sed 's/^$/./; s/^/ /' "$ROOT/LICENSE")
COPYRIGHT
printf 'slidelite (%s) stable; urgency=medium\n\n  * Release %s.\n\n -- Muhammad Ammar Qaisar <ammarqaisar1130@gmail.com>  %s\n' \
  "$VERSION" "$VERSION" "$(date -R)" | gzip -9n > "$DEST/usr/share/doc/slidelite/changelog.gz"

# Exact, non-world-writable modes.
find "$DEST" -type d -exec chmod 0755 {} +
find "$DEST" -type f -exec chmod 0644 {} +
chmod 0755 "$DEST/usr/bin/slidelite"

INSTALLED_SIZE="$(du -sk --exclude=DEBIAN "$DEST" | cut -f1)"
cat > "$DEST/DEBIAN/control" <<CONTROL
Package: slidelite
Version: $VERSION
Section: graphics
Priority: optional
Architecture: all
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.12), python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1, gir1.2-gdkpixbuf-2.0
Recommends: fonts-crosextra-carlito, fonts-crosextra-caladea, fonts-liberation, fonts-dejavu-core
Suggests: fonts-noto-cjk, fonts-urw-base35
Maintainer: Muhammad Ammar Qaisar <ammarqaisar1130@gmail.com>
Homepage: https://github.com/ammarqaisar11a55/SlideLite-Lightweight-PPTX-Viewer-for-Ubuntu
Description: lightweight, offline PowerPoint (.pptx) viewer
 SlideLite opens PowerPoint presentations and presents them without an
 office suite: themes, masters and layouts, text, shapes, pictures,
 tables, charts, SmartArt and metafiles, a thumbnail sidebar, zoom, and a
 fullscreen slide show with transitions and build animations.
 .
 It is a viewer only. Presentations are never modified, macros never run,
 and it works completely offline without accounts or telemetry.
CONTROL
chmod 0644 "$DEST/DEBIAN/control"

mkdir -p "$OUT_DIR"
# xz keeps the package readable by every Debian/Ubuntu dpkg (and Python's tarfile).
dpkg-deb -Zxz --root-owner-group --build "$DEST" "$OUT_DIR/$PKG.deb" >/dev/null
echo "$OUT_DIR/$PKG.deb"

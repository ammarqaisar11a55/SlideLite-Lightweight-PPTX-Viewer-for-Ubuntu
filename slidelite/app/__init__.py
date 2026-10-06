"""GTK application shell (window, header bar, WebKit view, host bridge).

GObject-introspection versions are pinned here: a package ``__init__`` always
runs before any of its modules, so every ``slidelite.app.*`` import gets GTK 3
and WebKitGTK 4.1 regardless of import ordering.
"""

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")

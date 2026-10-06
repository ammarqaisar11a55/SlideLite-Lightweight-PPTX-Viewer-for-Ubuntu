from slidelite.server.router import Response, Router


def test_serves_index():
    resp = Router().handle("/")
    assert resp.status == 200
    assert resp.mime == "text/html"
    assert b"Content-Security-Policy" in resp.body


def test_static_mime_types():
    router = Router()
    assert router.handle("/js/app.js").mime == "text/javascript"
    assert router.handle("/css/app.css").mime == "text/css"
    assert router.handle("/icons/slidelite.svg").mime == "image/svg+xml"


def test_path_traversal_is_refused():
    router = Router()
    for path in (
        "/../server/router.py",
        "/..%2f..%2fetc/passwd",
        "/js/../../cli.py",
        "/%2e%2e/cli.py",
    ):
        assert router.handle(path).status == 404, path


def test_missing_file():
    assert Router().handle("/nope.js").status == 404


def test_mounted_handler_and_errors_are_contained():
    router = Router()
    router.mount("/doc", lambda sub, q: Response(sub.encode(), "text/plain"))
    assert router.handle("/doc/abc/slide/1.html").body == b"abc/slide/1.html"

    def boom(sub, q):
        raise RuntimeError("bad slide")

    router.mount("/bad", boom)
    resp = router.handle("/bad/x")
    assert resp.status == 500 and b"bad slide" in resp.body

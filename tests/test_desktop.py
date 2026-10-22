"""The object handed to pywebview must expose only its browse functions (see desktop.Api)."""

import inspect

from rlclip import desktop


class DeepThing:
    """Stands in for the native window: walking into it never ends."""

    @property
    def child(self):
        return DeepThing()


def test_api_exposes_only_browse_functions():
    api = desktop.Api(window=DeepThing())
    public = [n for n in dir(api) if not n.startswith("_")]
    assert sorted(public) == ["browse", "browse_folder"]
    assert all(inspect.ismethod(getattr(api, n)) for n in public)


def test_backend_gets_its_own_port(tmp_path):
    from rlclip import app, config
    cfg = config.load()
    cfg["output_dir"] = str(tmp_path)
    _, url1 = app.start_server(cfg)
    _, url2 = app.start_server(cfg)
    assert url1 != url2


def test_port_in_use_is_refused():
    import socket
    from http.server import BaseHTTPRequestHandler
    import pytest
    from rlclip import app
    first = app._Server(("127.0.0.1", 0), BaseHTTPRequestHandler)
    with pytest.raises(OSError):
        app._Server(("127.0.0.1", first.server_address[1]), BaseHTTPRequestHandler)
    first.server_close()

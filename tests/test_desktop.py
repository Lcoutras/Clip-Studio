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

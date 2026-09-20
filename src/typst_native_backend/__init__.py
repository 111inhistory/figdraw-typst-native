from __future__ import annotations

from .backend import (
    TypstNativeConfig,
    clear_cache,
    configure,
    get_config,
    load_cache,
    register_rcparams,
    reset_config,
    save_cache,
)


def use(*, force: bool = True) -> None:
    import matplotlib

    matplotlib.use("module://typst_native_backend.backend", force=force)


__all__ = [
    "TypstNativeConfig",
    "clear_cache",
    "configure",
    "get_config",
    "load_cache",
    "register_rcparams",
    "reset_config",
    "save_cache",
    "use",
]

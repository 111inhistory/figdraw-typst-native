from __future__ import annotations

from .backend import (
    TypstNativeConfig,
    configure,
    get_config,
    register_rcparams,
    reset_config,
)


def use(*, force: bool = True) -> None:
    import matplotlib

    matplotlib.use("module://typst_native_backend.backend", force=force)


__all__ = [
    "TypstNativeConfig",
    "configure",
    "get_config",
    "register_rcparams",
    "reset_config",
    "use",
]

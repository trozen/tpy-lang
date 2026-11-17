# `xa`'s `__all__` lists y, which it only declares, and it starts `xb`.
__all__ = ["y"]
y: int
import xb  # noqa: E402

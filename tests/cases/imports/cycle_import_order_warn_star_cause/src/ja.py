# `ja`'s `__all__` lists y, which it only declares, and z, which it defines
# only after it starts `jb`.
__all__ = ["y", "z"]
y: int
import jb  # noqa: E402


def z() -> int:
    return 4

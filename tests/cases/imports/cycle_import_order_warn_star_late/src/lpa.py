# `_E` is bound above the cycle import, but `__all__`, which exports it, only
# below: CPython's star import in `lpb` copies public names only.
from enum import Enum


class _E(Enum):
    X = 7


import lpb  # noqa: E402
__all__ = ["_E"]

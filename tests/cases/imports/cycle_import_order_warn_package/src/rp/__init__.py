# Relative imports read the package and its submodules as absolute ones do.
from . import c


class X:
    def __init__(self, v: int) -> None:
        self.v = v

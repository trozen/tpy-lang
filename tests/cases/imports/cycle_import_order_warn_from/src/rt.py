class B:
    def __init__(self, k: int) -> None:
        self.k = k


# Only declared: `from rt import *` binds it under TPy, never under CPython.
z: int
# Code the module runs binds other names, never `z`.
_ready = len("ready")

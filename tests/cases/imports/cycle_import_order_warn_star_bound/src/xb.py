# A name `__all__` lists and the module only declares: defining it above the
# import would not help, so the remedy is a value outside the cycle.
from xa import *  # tpyc: warning(/`from xa import \*` reads 'y', listed in `__all__` of 'xa', while 'xa' is still being imported -- .*, and 'y' is only declared \(xa.py:3\), which binds nothing; CPython raises AttributeError \(partially initialized module 'xa' has no attribute 'y'\)\. Give 'y' a value in a module outside the cycle and import it in xa\.py above that import\./)


def main() -> None:
    print("declared in __all__:", y)

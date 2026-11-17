# Both causes are steps of one remedy: define z above the import, give y a
# value outside the cycle that `ja` imports above it.
from ja import *  # tpyc: warning(/`from ja import \*` reads 'y', 'z', listed in `__all__` of 'ja', while 'ja' is still being imported -- .* before it defines 'z' \(ja\.py:8\), and 'y' is only declared \(ja\.py:4\), which binds nothing; CPython raises AttributeError \(partially initialized module 'ja' has no attribute 'y'\)\. Define 'z' above that import in ja\.py and give 'y' a value in a module outside the cycle and import it in ja\.py above that import\./)


def main() -> None:
    print("declared and late:", y, z())

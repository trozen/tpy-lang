# The literal `__all__` standing at the star import lists nothing: warned.
from la import *  # tpyc: warning(/'la' is still being imported -- .* before its `__all__` update \(la\.py:10\) adds 'f'; CPython's star import does not bind it here, so a use of it raises NameError\./)


def main() -> None:
    print("literal __all__:", f())

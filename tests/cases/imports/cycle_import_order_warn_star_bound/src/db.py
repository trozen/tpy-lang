# The first cause the warning names is the declaration: x binds nothing.
from da import *  # tpyc: warning(/'da' is still being imported -- main.py imports 'da', and 'da' imports 'db' \(da.py:3\), and 'x' is only declared \(da.py:2\), which binds nothing; CPython's star import does not bind it here, so a use of it raises NameError\. Give 'x' a value in a module outside the cycle and import it from there\./)


def main() -> None:
    print("declared:", x)

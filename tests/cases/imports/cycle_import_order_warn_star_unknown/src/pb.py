# CPython fails at the explicit import; the star import below it adds no
# warning of its own.
from pa import E  # tpyc: warning(/'E' is imported from 'pa' while 'pa' is still being imported -- .* before it defines 'E' \(pa\.py:5\); CPython raises ImportError/)
from pa import *  # tpyc: ok


def main() -> None:
    print("explicit then star:", E().v)

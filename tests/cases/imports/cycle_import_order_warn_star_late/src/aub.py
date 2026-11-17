from aua import *  # tpyc: warning(/'aua' is still being imported -- .* before it defines 'F', and before its `__all__` update \(aua.py:11\) adds 'F'.*Define 'F' above that import in aua.py and move the `__all__` update \(aua.py:11\) above that import/)


def f(x: F) -> int:
    return x.v

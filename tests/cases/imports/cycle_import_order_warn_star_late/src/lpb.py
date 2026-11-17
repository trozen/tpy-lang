from lpa import *  # tpyc: warning(/'lpa' is still being imported -- .* before it binds `__all__`, which lists '_E'; CPython's star import does not bind it here.*Define `__all__` above that import in lpa.py/)


def f() -> int:
    return _E.X.value

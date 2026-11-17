# t2 is still being imported while t3 runs, but has bound B already.
from t2 import B  # tpyc: ok
from tpy import Own


def three() -> Own[B]:
    return B(3)

# A nested def bound in one arm and called in the other: the name is a local
# of the whole function that this path never binds (CPython: UnboundLocalError
# when `k` is False), so the call is refused as a read no binding reaches --
# the definite-assignment rule (docs/LANGUAGE_FEATURES.md
# "Definite-Assignment Analysis").
from tpy import int32


def f(k: bool) -> int32:
    if k:
        def h() -> int32:
            return 1
        return h()
    else:
        return h()  # tpyc: error(/variable 'h' may not be assigned at this point/)


print(f(True))

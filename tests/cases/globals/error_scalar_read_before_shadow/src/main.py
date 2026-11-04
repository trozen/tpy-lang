# A scalar global read before the body binds the same name: the binding makes
# it a local of the whole body, so the read is CPython's UnboundLocalError,
# never the module global.
from tpy import int32

COUNT: int32 = 3


def f() -> int32:
    x = COUNT  # tpyc: error(/'COUNT' is read before it is bound on line 11: .* does not see the module's 'COUNT'/)
    COUNT = 5
    return x + COUNT


print(f())

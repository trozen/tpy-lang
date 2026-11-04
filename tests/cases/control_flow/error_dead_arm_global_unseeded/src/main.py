# A `global` in an arm that never runs still declares the name global for
# the whole function, so the write below rebinds the module's tuple and is
# refused as the live declaration is (BUGS.md#value-union-global-rebind-rejects).
from tpy import int32

t: tuple[int32, int32] = (1, 2)


def f() -> None:
    if False:
        global t  # tpyc: error(/not yet supported by C\+\+ code generation/)
    t = (3, 4)


f()
print(t)

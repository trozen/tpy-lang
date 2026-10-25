# A value read of an integer constant generic `N` is a default-int value: at
# g[T] / g(int64) the generic variant binds T = int32 (an exact match) and
# wins. Its argument then stops at lowering, as for a single generic callee
# (BUGS.md#scalar-rvalue-at-bare-generic-slot).
from tpy import dispatch, int64


@dispatch
def g[T](x: T) -> str:
    return "generic"


@dispatch
def g(x: int64) -> str:
    return "int64"


class Box[N: int]:
    def run(self) -> None:
        print(g(N))  # tpyc: error(/call.generic_arg_shape/)


Box[3]().run()

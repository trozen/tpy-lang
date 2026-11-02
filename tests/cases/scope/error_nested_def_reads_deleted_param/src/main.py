# A nested def reading a parameter the function deleted: the parameter starts
# assigned and the `del` unbinds it, so the closure's read is CPython's
# NameError at the call.
from tpy import int32


def f(xs: list[int32]) -> None:
    del xs

    def inner() -> int32:
        # The capture of the deleted parameter.
        return xs[0]  # tpyc: error(/variable 'xs' may not be assigned at this point/)
    print(inner())


f([1])

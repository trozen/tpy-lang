# The zero-argument call `with` makes over an `__enter__` @dispatch set is
# resolved like any call: a defaulted variant beside a no-parameter one
# matches it equally, so the `with` is refused as ambiguous (the tie rule of
# docs/LANGUAGE_FEATURES.md "Overloading": a genuine tie is an error, never a
# declaration-order pick).
from tpy import dispatch, int32


class M:
    def __init__(self) -> None:
        pass

    @dispatch
    def __enter__(self, x: int32 = 5) -> int32:
        return x

    @dispatch
    def __enter__(self) -> int32:
        return 7

    def __exit__(self, kind: None, value: None, tb: None) -> None:
        pass


def main() -> None:
    m = M()
    with m as v:  # tpyc: error(/Ambiguous overload for '__enter__'/)
        print("with", v)


main()

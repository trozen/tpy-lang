# A readonly receiver ranks the readonly variants first (docs/OVERLOAD_DESIGN.md
# "The receiver kind ranks first"); when only a mutating one takes the key, the
# subscript is that call on a readonly receiver, refused as the call would be.
from tpy import dispatch, int32, readonly


class F:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly(False)
    def __getitem__(self, k: str) -> int32:
        self.hits += 1
        return 200

    @dispatch
    @readonly
    def __getitem__(self, k: int32) -> int32:
        return 101


def f(b: readonly[F]) -> None:
    # only the mutating variant takes a str key
    print(b["s"])  # tpyc: error(/Cannot call non-readonly method '__getitem__' on readonly reference/)


def main() -> None:
    f(F())


main()

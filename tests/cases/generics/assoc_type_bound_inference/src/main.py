# Fully-inferred generic call with a sibling-referencing protocol bound:
# R in `unwrap[R, T: Container[R]]` is solved from the concrete conformer's
# method signature (associated-type inference) -- no explicit type args.
from typing import Protocol


class Container[R](Protocol):
    def get(self) -> R: ...


class IntBox:
    v: int

    def __init__(self, v: int):
        self.v = v

    def get(self) -> int:
        return self.v


class StrBox:
    s: str

    def __init__(self, s: str):
        self.s = s

    def get(self) -> str:
        return self.s


def unwrap[R, T: Container[R]](x: T) -> R:
    return x.get()


def main() -> None:
    n = unwrap(IntBox(42))          # tpyc: ok
    print(n + 1)
    print(unwrap(StrBox("hello")))  # tpyc: ok


main()

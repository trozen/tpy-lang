# A widened int32 at an operator dunder's `Base | int64` parameter is no object
# the pointer variant can lend (BUGS.md#ptr-union-literal-arg-as-const).
from tpy import int32, int64


class Base:
    def __init__(self, n: int32) -> None:
        self.n = n


class Mixed:
    def __init__(self) -> None:
        pass

    def __add__(self, o: Base | int64) -> int64:
        if isinstance(o, Base):
            return o.n
        return o + 100


def run(i: int32) -> None:
    m = Mixed()
    print(m + i)  # tpyc: error(/operator.operand.borrow_source/)


run(2)

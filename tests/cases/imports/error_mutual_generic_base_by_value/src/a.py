from tpy import int32
import b
import h


# the subject: Hold[Item] stores the Item inside A
class A(h.Hold[b.Item]):  # tpyc: error(/Cyclic import: 'A' in 'a' inherits from 'Hold\[Item\]'/)
    def __init__(self, x: b.Item) -> None:
        super().__init__(x)


def run() -> int32:
    return A(b.Item(4)).value.n

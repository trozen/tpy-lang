from tpy import int32
import b
import h


class A:
    x: h.Handle[b.Item]  # tpyc: error(/field 'A.x' names 'Handle\[Item\]', a generic class over 'Item'/)

    def __init__(self) -> None:
        self.x = h.Handle[b.Item]()


def run() -> int32:
    return 1 if A().x.ptr is None else 0

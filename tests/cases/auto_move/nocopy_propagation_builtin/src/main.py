# Class containing UninitHeapStorage (builtin nocopy) becomes implicitly nocopy
from tpy import Int32, UInt32, Own
from tpy.mem import UninitHeapStorage


class Storage:
    buf: UninitHeapStorage[Int32]

    def __init__(self):
        self.buf = UninitHeapStorage[Int32](UInt32(1))
        self.buf.init0(42)

    def __del__(self):
        self.buf.drop0()

    def get(self) -> Int32:
        return self.buf.load0()


def consume(s: Own[Storage]) -> Int32:
    return s.get()


def main():
    s = Storage()
    print(consume(s))  # tpyc: ok


main()

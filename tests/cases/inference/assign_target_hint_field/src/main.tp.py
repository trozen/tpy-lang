# Field assignment inference: generic type args deduced from field annotation.
# Covers no-arg constructors (Array, list, StaticList) and with-arg (UninitHeapStorage).
from tpy import Int32, Array, StaticList, Own
from tpy.mem import UninitHeapStorage

class WithArray[T, N: int]:
    data: Array[T, N]

    def __init__(self):
        self.data = Array()  # tpyc: ok -- infer Array[T, N]

class WithList[T]:
    items: list[T]

    def __init__(self):
        self.items = list()  # tpyc: ok -- infer list[T]

class WithStaticList[T]:
    buf: StaticList[T, 4]

    def __init__(self):
        self.buf = StaticList()  # tpyc: ok -- infer StaticList[T, 4]

class WithHeapStorage[T]:
    _storage: UninitHeapStorage[T]

    def __init__(self, val: Own[T]):
        self._storage = UninitHeapStorage(1)  # tpyc: ok -- infer UninitHeapStorage[T]
        self._storage.init0(val)

    def __del__(self):
        self._storage.drop0()

    def get(self) -> T:
        return self._storage.load0()

def main():
    a = WithArray[Int32, 3]()
    a.data[0] = Int32(10)
    print("array:", a.data[0])

    l = WithList[Int32]()
    l.items.append(Int32(42))
    print("list:", l.items)

    s = WithStaticList[Int32]()
    s.buf.append(Int32(99))
    print("static_list:", s.buf)

    h = WithHeapStorage[Int32](Int32(7))
    print("heap:", h.get())

    print("done")

main()

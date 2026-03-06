# Field assignment inference: generic type args deduced from field annotation.
# Covers no-arg constructors (Array, list, dict) and with-arg (UninitHeapStorage).
from tpy import Int32, Array, Own
from tpy.mem import UninitHeapStorage

class WithArray[T, N: int]:
    data: Array[T, N]

    def __init__(self):
        self.data = Array()  # tpyc: ok -- infer Array[T, N]

class WithList[T]:
    items: list[T]

    def __init__(self):
        self.items = list()  # tpyc: ok -- infer list[T]

class WithDict[K, V]:
    data: dict[K, V]

    def __init__(self):
        self.data = dict()  # tpyc: ok -- infer dict[K, V]

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

    d = WithDict[str, Int32]()
    d.data["x"] = Int32(99)
    print("dict:", d.data)

    h = WithHeapStorage[Int32](Int32(7))
    print("heap:", h.get())

    print("done")

main()

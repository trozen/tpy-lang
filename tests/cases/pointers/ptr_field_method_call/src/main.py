# Test calling builtin-type methods through Ptr-typed fields (-> vs . in C++).
from tpy import Int32, UInt32, Ptr
from tpy.mem import UninitHeapStorage


class Wrapper[T]:
    _storage: Ptr[UninitHeapStorage[T]]

    def __init__(self, storage: Ptr[UninitHeapStorage[T]]) -> None:
        self._storage = storage

    def load_at(self, index: UInt32) -> T:
        return self._storage.load(index)


def main() -> None:
    storage = UninitHeapStorage[Int32](UInt32(4))
    storage.init(UInt32(0), 42)
    storage.init(UInt32(1), 99)

    w = Wrapper[Int32](Ptr(storage))
    print(w.load_at(UInt32(0)))
    print(w.load_at(UInt32(1)))

    storage.drop(UInt32(0))
    storage.drop(UInt32(1))

main()

# Test calling builtin-type methods through Ptr-typed fields (-> vs . in C++).
from tpy import int32, uint32, Ptr
from tpy.mem import UninitHeapStorage


class Wrapper[T]:
    _storage: Ptr[UninitHeapStorage[T]]

    def __init__(self, storage: Ptr[UninitHeapStorage[T]]) -> None:
        self._storage = storage

    def load_at(self, index: uint32) -> T:
        return self._storage.load(index)


def main() -> None:
    storage = UninitHeapStorage[int32](uint32(4))
    storage.init(uint32(0), 42)
    storage.init(uint32(1), 99)

    w = Wrapper[int32](storage)
    print(w.load_at(uint32(0)))
    print(w.load_at(uint32(1)))

    storage.drop(uint32(0))
    storage.drop(uint32(1))

main()

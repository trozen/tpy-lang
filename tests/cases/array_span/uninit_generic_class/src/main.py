# Test UninitHeapStorage used inside a generic class (exercises const T& overload).
from tpy import int32, Own
from tpy.mem import UninitHeapStorage

class Holder[T]:
    _storage: UninitHeapStorage[T]

    def __init__(self, value: T):
        self._storage = UninitHeapStorage[T](1)
        self._storage.init0(value)

    def get(self) -> T:
        return self._storage.load0()

    def set(self, value: T) -> None:
        self._storage.drop0()
        self._storage.init0(value)

    def take(self) -> Own[T]:
        return self._storage.take0()

# Value type
h = Holder[int32](42)
print(h.get())
h.set(100)
print(h.take())

# str
s = Holder[str]("hello")
print(s.get())
s.set("world")
print(s.take())

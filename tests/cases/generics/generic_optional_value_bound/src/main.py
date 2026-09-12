# T: ValueType bound should use std::optional<T> (not T*) for Optional[T].
from tpy import int32, ValueType

class Box[T: ValueType]:
    _value: T
    _has: bool
    def __init__(self, value: T) -> None:
        self._value = value
        self._has = True
    def get(self) -> T | None:
        if self._has:
            return self._value
        return None
    def clear(self) -> None:
        self._has = False

def main() -> None:
    b = Box[int32](42)
    v = b.get()  # tpyc: type(int32 | None)
    if v is not None:
        print("got:", v)

    b.clear()
    v2 = b.get()
    if v2 is None:
        print("cleared: ok")

    b2 = Box[bool](True)
    v3 = b2.get()
    if v3 is not None:
        print("bool:", v3)

main()

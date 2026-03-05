# Generic Optional codegen: T | None uses T* in generic templates (reference into
# stored data), while concrete value-type Optional (e.g. Int32 | None) uses std::optional.
from typing import Optional
from tpy import Int32

class Container[T]:
    _val: T | None

    def __init__(self, val: T | None):
        self._val = val

    def get(self) -> T | None:
        return self._val

    def set(self, val: T | None) -> None:
        self._val = val

def maybe_val(x: Optional[Int32]) -> Optional[Int32]:
    return x

def main():
    # Generic Optional with value type
    c = Container[Int32](Int32(42))
    v = c.get()
    if v is not None:
        print("got:", v)
    else:
        print("got: None")

    c.set(None)
    v2 = c.get()
    if v2 is not None:
        print("after set:", v2)
    else:
        print("after set: None")

    c.set(Int32(99))
    v3 = c.get()
    if v3 is not None:
        print("restored:", v3)

    # None-initialized container
    c2 = Container[Int32](None)
    v4 = c2.get()
    if v4 is None:
        print("none init: ok")

    # Optional[T] from typing (equivalent to T | None)
    r = maybe_val(Int32(7))
    if r is not None:
        print("maybe:", r)

    r2 = maybe_val(None)
    if r2 is None:
        print("maybe None: ok")

    print("done")

main()

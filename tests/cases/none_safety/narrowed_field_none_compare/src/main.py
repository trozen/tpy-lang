# A None-compare on an already-NARROWED storage-form Optional field must keep
# .has_value() semantics (the C++ shape stays std::optional<T>).
class Pod:
    x: int

    def __init__(self) -> None:
        self.x = 0


class T:
    o: Pod | None
    v: int | None

    def __init__(self) -> None:
        self.o = None
        self.v = None


def main() -> None:
    t = T()
    t.o = Pod()
    assert t.o is not None       # un-narrowed baseline: .has_value()
    t.o.x = 123
    assert t.o is not None       # tpyc: ok
    print(t.o.x)
    t.o = None
    if t.o is None:              # narrowed-to-None compare, storage render
        print("nil")
    t.v = 7
    assert t.v is not None       # un-narrowed baseline (value payload)
    print(t.v)
    assert t.v is not None       # tpyc: ok
    print(t.v)


main()

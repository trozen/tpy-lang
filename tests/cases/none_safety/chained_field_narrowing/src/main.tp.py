from tpy import Int32, copy


class Inner:
    value: Int32 | None

    def __init__(self, value: Int32):
        self.value = value


class Outer:
    inner: Inner

    def __init__(self, inner: Inner):
        self.inner = copy(inner)


def read(o: Outer) -> Int32:
    if o.inner.value is not None:
        return o.inner.value + 1  # tpyc: ok
    return 0


o1 = Outer(Inner(7))
o2 = Outer(Inner(1))
o2.inner.value = None
print(read(o1))
print(read(o2))

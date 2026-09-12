# Only Own[T] params get std::move; non-Own params are passed normally.
from tpy import int32, Own


class Inner:
    value: int32


class Holder:
    inner: Inner

    def set_with_tag(self, inner: Own[Inner], tag: int32) -> None:
        self.inner = inner
        self.inner.value = self.inner.value + tag


class GenericHolder[T]:
    item: T

    def __init__(self, item: Own[T]):
        self.item = item

    def replace_with_flag(self, item: Own[T], flag: int32) -> int32:
        self.item = item
        return flag


class GenericBox[T]:
    item: T

    def __init__(self, item: T):
        self.item = item


def main():
    h = Holder()
    h.inner = Inner()

    # Method with Own + non-Own: only Own param should be moved
    i = Inner()
    i.value = 10
    h.set_with_tag(i, int32(5))
    print(h.inner.value)

    # Generic ctor with non-Own param: should NOT move
    i2 = Inner()
    i2.value = 42
    box = GenericBox[Inner](i2)
    print(box.item.value)
    print(i2.value)

    # Generic method with Own + non-Own: only Own should move
    gh = GenericHolder[Inner](Inner())
    i3 = Inner()
    i3.value = 7
    f = gh.replace_with_flag(i3, int32(3))
    print(gh.item.value)
    print(f)


main()

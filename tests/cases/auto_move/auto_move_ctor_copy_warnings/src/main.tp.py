# Auto-move suppresses copy warnings for field/subscript assigns at last use.
# Warnings must still fire when the variable is NOT at its last use.
from tpy import Int32, Own


class Inner:
    value: Int32


class Holder:
    inner: Inner

    def __init__(self, inner: Own[Inner]):
        self.inner = inner  # tpyc: ok (ctor own-field, last use -- auto-moved)

    def set_inner(self, inner: Own[Inner]) -> None:
        self.inner = inner  # tpyc: ok (field assign, last use -- auto-moved)


class NotLastUse:
    inner: Inner

    def __init__(self, inner: Own[Inner]):
        self.inner = inner  # tpyc: warning(/copies.*field/)
        print(inner.value)


class GenericHolder[T]:
    item: T

    def __init__(self, item: Own[T]):
        self.item = item  # tpyc: ok (generic ctor, last use -- std::move)

    def set_item(self, item: Own[T]) -> None:
        self.item = item  # tpyc: ok (generic method, last use -- std::move)


class GenericNotLastUse[T]:
    item: T

    def __init__(self, item: Own[T]):
        self.item = item  # tpyc: warning(/may copy.*field/)
        print(item)


class OptHolder:
    inner: Inner | None

    def set(self, inner: Own[Inner]) -> None:
        self.inner = inner  # tpyc: ok (optional field assign, last use -- auto-moved)

    def set_not_last(self, inner: Own[Inner]) -> None:
        self.inner = inner  # tpyc: warning(/copies.*field/)
        print(inner.value)


class Outer:
    inner: Inner


def test_nested_field_move(o: Outer, inner: Own[Inner]) -> None:
    o.inner = inner  # tpyc: ok (nested field assign, last use -- auto-moved)


def test_nested_field_copy(o: Outer, inner: Own[Inner]) -> None:
    o.inner = inner  # tpyc: warning(/copies.*field/)
    print(inner.value)


def test_subscript_move(xs: list[Inner], inner: Own[Inner]) -> None:
    xs[0] = inner  # tpyc: ok (subscript assign, last use -- auto-moved)


def test_subscript_copy(xs: list[Inner], inner: Own[Inner]) -> None:
    xs[0] = inner  # tpyc: warning(/copies.*container/)
    print(inner.value)


def main():
    # Ctor with rvalue
    h = Holder(Inner())
    print(h.inner.value)

    # Method with lvalue
    i = Inner()
    i.value = 10
    h.set_inner(i)
    print(h.inner.value)

    # Generic ctor with lvalue
    i2 = Inner()
    i2.value = 20
    gh = GenericHolder[Inner](i2)
    print(gh.item.value)

    # Generic set_item with lvalue
    i3 = Inner()
    i3.value = 30
    gh.set_item(i3)
    print(gh.item.value)

    # Generic set_item with rvalue
    gh.set_item(Inner())
    print(gh.item.value)


main()

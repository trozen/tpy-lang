from tpy import int32, Own, copy


class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y


class Holder:
    value: Point | None

    def __init__(self) -> None:
        self.value = None


def make_holder(p: Point) -> Own[Holder]:
    h = Holder()
    h.value = copy(p)
    return copy(h)  # tpyc: warning(/unnecessary copy/)


def test_init_from_temp() -> None:
    p = Point(1, 2)
    # Field access on Own[Holder] return (temporary) — must not dangle
    v: Point | None = make_holder(p).value
    print(v is not None)
    print(v.x)
    print(v.y)


def test_rebind_from_temp() -> None:
    v: Point | None = None
    p = Point(3, 4)
    # Rebind pointer-local from field of temporary
    v = make_holder(p).value
    print(v is not None)
    print(v.x)


def test_rebind_in_block() -> None:
    v: Point | None = None
    p = Point(5, 6)
    # Rebind inside if-block — slot must survive block exit
    if True:
        v = make_holder(p).value
    # v must still be valid here (slot hoisted to function scope)
    print(v is not None)
    print(v.x)
    print(v.y)


test_init_from_temp()
test_rebind_from_temp()
test_rebind_in_block()

# A class with no `__init__` (and only defaulted fields) inherits its parent's;
# `super().__init__(...)` through one reaches the ancestor's `__init__`, and
# naming that ancestor (`Box.__init__(self, ...)`) initializes the direct base.
from tpy import int32

class Box:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    def get(self) -> int32:
        return self.value


class IntBox(Box):
    pass


class TaggedBox(IntBox):
    pass


class LabeledBox(Box):
    label: str = "box"


class Doubled(LabeledBox):
    def __init__(self, n: int32) -> None:
        # resolves to `Box.__init__` through `__init__`-less LabeledBox
        super().__init__(n * 2)  # tpyc: ok


class NamedGrand(LabeledBox):
    def __init__(self, n: int32) -> None:
        # naming the ancestor past the `__init__`-less direct base runs exactly
        # that base's inherited initializer, so it constructs LabeledBox
        Box.__init__(self, n + 1)  # tpyc: ok


class NamedMid(TaggedBox):
    def __init__(self, n: int32) -> None:
        # the named class is `__init__`-less itself: TaggedBox runs Box's too
        IntBox.__init__(self, n + 2)  # tpyc: ok


class Opt:
    level: int32

    def __init__(self, level: int32 = 1) -> None:
        self.level = level


class OptMid(Opt):
    pass


class OptLeaf(OptMid):
    def __init__(self) -> None:
        # an all-defaulted ancestor `__init__` reached through a `pass` class
        super().__init__(5)  # tpyc: ok


class Holder[U]:
    item: U

    def __init__(self, item: U) -> None:
        # The copy is unobservable here: every caller passes a fresh literal.
        self.item = item  # tpyc: warning(/may copy U into field/)


class ListHolder[T](Holder[list[T]]):
    pass


class ViaSuper(ListHolder[int32]):
    def __init__(self, xs: list[int32]) -> None:
        # the ancestor's `U` binds through the parent's arguments: list[int32]
        super().__init__(xs)  # tpyc: ok


class ViaName(ListHolder[int32]):
    def __init__(self, xs: list[int32]) -> None:
        # the same resolution for the named-parent spelling
        ListHolder.__init__(self, xs)  # tpyc: ok


class AppError(Exception):
    pass


class SubError(AppError):
    def __init__(self, code: int32) -> None:
        # a native grandparent's `__init__` through an `__init__`-less class
        super().__init__(f"sub {code}")  # tpyc: ok
        self.code = code


def main() -> None:
    a = IntBox(7)
    print(a.get())

    b = TaggedBox(42)
    print(b.value)

    # a defaulted own field does not stop the inheritance
    lb = LabeledBox(9)
    print("defaulted field:", lb.value, lb.label)
    d = Doubled(4)
    print("through fields:", d.value, d.label)
    ng = NamedGrand(10)
    print("named grandparent:", ng.value, ng.label)
    print("named pass class:", NamedMid(10).value)
    print("through pass:", OptLeaf().level, OptMid().level)
    vs = ViaSuper([1, 2])
    vn = ViaName([3])
    print("generic ancestor:", vs.item, vn.item)
    try:
        raise SubError(3)
    except SubError as e:
        print("native grandparent:", str(e), e.code)


main()

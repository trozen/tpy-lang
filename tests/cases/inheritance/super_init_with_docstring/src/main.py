# What may sit around a parent-initializer call without displacing it from
# the first-statement position the rule demands (error_super_not_first,
# error_late_explicit_parent_init): a docstring or `pass` before it, a nested
# `def` after it, `pass` between calls and field inits -- for
# `super().__init__()` and `Base.__init__(self)` alike.
from tpy import int32


class Parent:
    value: int32

    def __init__(self, value: int32) -> None:
        print("parent init", value)
        self.value = value


class Other:
    m: int32

    def __init__(self, m: int32) -> None:
        print("other init", m)
        self.m = m


# docstring before super().__init__()
class Child(Parent):
    extra: int32

    def __init__(self, value: int32, extra: int32) -> None:
        """Initialize Child with value and extra."""
        super().__init__(value)  # tpyc: ok
        self.extra = extra


# nested def after super().__init__()
class Nested(Parent):
    extra: int32

    def __init__(self, value: int32, extra: int32) -> None:
        super().__init__(value)

        def bonus() -> int32:  # tpyc: ok
            return 5

        self.extra = extra + bonus()


# `pass` before super().__init__(), then an own field init
class PassSuper(Parent):
    y: int32

    def __init__(self) -> None:
        pass
        super().__init__(1)  # tpyc: ok
        self.y = 3
        print("pass-super: after", self.value, self.y)


# `pass` before Base.__init__(self)
class PassBase(Parent):
    def __init__(self) -> None:
        pass
        Parent.__init__(self, 2)  # tpyc: ok
        print("pass-base: after", self.value)


# `pass` before a multi-base child's consecutive per-base calls
class PassMulti(Parent, Other):
    def __init__(self) -> None:
        pass
        Parent.__init__(self, 3)
        Other.__init__(self, 4)  # tpyc: ok
        print("pass-multi: after", self.value, self.m)


# a docstring AND `pass` before super().__init__()
class DocPass(Parent):
    def __init__(self) -> None:
        """Docstring, then pass, then the parent call."""
        pass
        super().__init__(5)  # tpyc: ok
        print("doc-pass: after", self.value)


# `pass` between a multi-base child's per-base calls and between field inits
class PassBetween(Parent, Other):
    a: int32
    b: int32

    def __init__(self) -> None:
        Parent.__init__(self, 6)
        pass
        Other.__init__(self, 7)  # tpyc: ok
        pass
        self.a = 8
        pass
        self.b = 9  # tpyc: ok
        print("pass-between: after", self.value, self.m, self.a, self.b)


def main() -> None:
    c = Child(10, 20)
    print("docstring:", c.value, c.extra)
    n = Nested(3, 4)
    print("nested-def:", n.value, n.extra)
    p = PassSuper()
    print("pass-super:", p.value, p.y)
    b = PassBase()
    print("pass-base:", b.value)
    m = PassMulti()
    print("pass-multi:", m.value, m.m)
    dp = DocPass()
    print("doc-pass:", dp.value)
    pb = PassBetween()
    print("pass-between:", pb.value, pb.m, pb.a, pb.b)


main()

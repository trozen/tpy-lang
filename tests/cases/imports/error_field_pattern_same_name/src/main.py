# A class pattern on a plain field whose class only shares the field type's
# name names both classes qualified, so the message tells them apart. Rule:
# LANGUAGE_FEATURES.md match "Error diagnostics" (a class sub-pattern that
# can never match its field; CPython runs it as an arm that never fires).
import m


class Outer:
    class Inner:
        v: int

        def __init__(self) -> None:
            self.v = 1


class H:
    p: Outer.Inner

    def __init__(self) -> None:
        self.p = Outer.Inner()


def f(h: H) -> str:
    match h:
        case H(p=m.Outer.Inner()):  # tpyc: error(/type pattern 'm.Outer.Inner' does not match field type 'Outer.Inner' \(different type, same name\)/)
            return "m"
        case _:
            return "other"


print(f(H()))

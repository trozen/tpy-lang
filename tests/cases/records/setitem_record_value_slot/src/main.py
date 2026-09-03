# A user record whose __setitem__ takes a plain (non-Own) RECORD value: the
# parameter binds const Data&, so a still-live name passes by reference and a
# ctor rvalue binds the same slot directly. Nothing is copied at the call.
#
# The copies are INSIDE __setitem__, at its field writes (`self.a = value`),
# where sema warns on those lines: TPy stores a copy where CPython would
# alias, so the case never mutates `z` after the write. Sema ALSO warns
# "copies Data into container" at the call site, which misattributes the
# callee's copy to the caller (BUGS.md#setitem-copy-warning-at-call-site);
# the annotation below pins that warning as currently emitted, not as right.
from tpy import Int32


class Data:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v


class Slots:
    a: Data
    b: Data

    def __init__(self) -> None:
        self.a = Data(0)
        self.b = Data(0)

    def __setitem__(self, index: Int32, value: Data) -> None:
        if index == 0:
            self.a = value
        else:
            self.b = value

    def __getitem__(self, index: Int32) -> Data:
        if index == 0:
            return self.a
        return self.b


def fill(s: Slots) -> None:
    z = Data(5)
    s[0] = z  # tpyc: warning(/copies Data into container/)  the misattributed one
    s[1] = Data(9)  # a ctor rvalue binds the same slot
    print(z.value)


def main() -> None:
    s = Slots()
    fill(s)
    print(s[0].value, s[1].value)


main()

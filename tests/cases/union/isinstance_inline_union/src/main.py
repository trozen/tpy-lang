# Inline-union isinstance `isinstance(v, A | B)` -- the CPython-valid analog
# of a type-alias name (which CPython rejects at runtime). Verifies positive
# (branch taken) and negative (else narrows v to the excluded member C, whose
# field is then read) narrowing, mirroring the tuple-form test.
from tpy import Int32


class A:
    tag: Int32

    def __init__(self, tag: Int32) -> None:
        self.tag = tag


class B:
    tag: Int32

    def __init__(self, tag: Int32) -> None:
        self.tag = tag


class C:
    z: Int32

    def __init__(self, z: Int32) -> None:
        self.z = z


def classify(v: A | B | C) -> str:
    if isinstance(v, A | B):
        return "ab"
    return "c"


def excluded(v: A | B | C) -> Int32:
    if not isinstance(v, A | B):
        return v.z
    return -1


def main() -> None:
    print(classify(A(1)))
    print(classify(C(3)))
    print(excluded(C(9)))
    print(excluded(A(1)))


main()

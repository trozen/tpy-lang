# The boundary the reference-axis ternary must NOT cross: a receiver ternary
# is admitted only when BOTH arms are lvalue NAMEs. A mixed lvalue/prvalue
# pair makes the C++ `?:` a prvalue that would silently copy the named arm
# (BUGS.md#ternary-receiver-mixed-category-copies), so the mutation would land
# on the copy. Widening the arm's family gate to the reference axis (which is
# what let `bytearray` reach the NAME-arm ternary at all) must not widen this.
from tpy import Int32, Own


def make() -> Own[bytearray]:
    return bytearray(b"z")


def push(c: bool, a: bytearray) -> Int32:
    (a if c else make()).append(1)  # tpyc: error(/method.recv.other/)
    return len(a)


def main() -> None:
    print(push(True, bytearray(b"q")))


main()

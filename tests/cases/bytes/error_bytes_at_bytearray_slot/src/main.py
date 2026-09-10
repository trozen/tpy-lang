# A `bytes` value bound to a `bytearray` slot is a located error. CPython does
# not enforce the annotation -- `ba` stays the same immutable object and
# `ba.append` raises -- so copying silently here would make the TPy program
# mutate a private buffer where CPython raises. It carries no `bytearray(...)`
# remediation, unlike the bytes-sink direction: that one is told to copy because
# the pair DOES convert where the destination borrows, and this pair converts
# nowhere -- an immutable buffer cannot back a mutable slot in any position.
def f(p: bytes) -> None:
    ba: bytearray = p  # tpyc: error(/expected bytearray, got bytes/)
    ba.append(3)
    print(len(p), len(ba))


def main() -> None:
    f(b"ab")


main()

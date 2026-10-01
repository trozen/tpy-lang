# A documented divergence (docs/LANGUAGE_FEATURES.md "Numeric widening across
# reassignments"): a local's first binding gives its type, so a float literal
# stored into a local first bound float32 rounds to float32 and a local first
# bound uint8 wraps; CPython keeps the literal as a double and its int never
# wraps.
from tpy import float32, uint8


def main() -> None:
    f = float32(0.1)  # tpyc: type(float32)
    # the literal rounds to float32: 0.30000001192092896 where CPython prints
    # 0.30000000000000004
    f = 0.1  # tpyc: ok
    print("float32", f * 3)
    u = uint8(3)  # tpyc: type(uint8)
    # the literal converts into the uint8 local: ~u is 255 where CPython
    # prints -1
    u = 0  # tpyc: ok
    print("uint8", ~u)


main()

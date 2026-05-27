# A *unpack must be the sole entry in the vararg region. Mixing it with
# other positional args (`f(a, *xs, b)`) previously silently dropped the
# non-unpacked args (codegen's _gen_vararg_pack returns on first unpack).
# Now rejected cleanly at sema until codegen concatenates positionals with
# the unpacked span (see TODO.md).
from tpy import Int32


def sum_all(*xs: Int32) -> Int32:
    total: Int32 = 0
    for x in xs:
        total += x
    return total


def main() -> None:
    mid: list[Int32] = [10, 20]
    print(sum_all(1, *mid, 2))  # tpyc: error(/Cannot mix \*unpacking/)


main()

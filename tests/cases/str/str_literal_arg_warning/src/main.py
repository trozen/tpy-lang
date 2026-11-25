# A wide int literal passed to a protocol parameter (`Stringable`) settles at
# int (BigInt) by the default-int rule and warns as a binding does; a
# parameter that fixes the type, or an in-range literal, stays silent.
from tpy import Stringable
import part_positions
import part_silent



def one(x: Stringable) -> str:
    return str(x)

# module level: a bare literal and a list display
print("mod", str(5000000000))  # tpyc: warning(/passed as int \(BigInt\)/)
print("mod", one([5000000014]))  # tpyc: warning(/literal 5000000014 .*passed as int \(BigInt\)/)
# module level: each literal element of a list repetition
print("mod", one([5000000015] * 2), str([5000000016] * 2))  # tpyc: warning(/literal 5000000015 /) warning(/literal 5000000016 /)


def shapes() -> None:
    big: int = 7
    # a bare literal, and two equal literals on one line: one warning each
    print("bare", str(5000000000))  # tpyc: warning(/passed as int \(BigInt\)/)
    print("two", str(5000000000), str(5000000000))  # tpyc: warning(/passed as int \(BigInt\)/) warning(/passed as int \(BigInt\)/)
    # folded constants warn with their folded value
    print("fold", str(-2**63))  # tpyc: warning(/literal -9223372036854775808 .*passed as int \(BigInt\)\. Write int64/)
    print("fold", str(2**70))  # tpyc: warning(/literal 1180591620717411303424 .*Write int\(1180591620717411303424\)/)
    print("fold", str(1 << 40), str(5000000000 + 1))  # tpyc: warning(/literal 1099511627776 /) warning(/literal 5000000001 /)
    print("fold", str(-5000000000))  # tpyc: warning(/literal -5000000000 .*passed as int \(BigInt\)/)
    # each wide element of a tuple display, nested tuples included
    print("tuple", str((1, 5000000000)))  # tpyc: warning(/passed as int \(BigInt\)/)
    print("tuple", one((1, (2, 6000000000))))  # tpyc: warning(/literal 6000000000 .*passed as int \(BigInt\)/)
    # each wide element of a list display whose elements are literals
    print("list", one([5000000000]))  # tpyc: warning(/passed as int \(BigInt\)/)
    print("list", one([[7000000000]]))  # tpyc: warning(/literal 7000000000 .*passed as int \(BigInt\)/)
    # each literal element of a list repetition, as of the display
    print("repeat", one([5000000003] * 2), str([5000000004, 1] * 2))  # tpyc: warning(/literal 5000000003 .*passed as int \(BigInt\)/) warning(/literal 5000000004 /)
    # a repetition of a typed element holds no literal leaf
    print("repeat", one([big] * 2))  # tpyc: ok
    # a display settled at int by a typed element holds no literal leaf
    print("list", one([big, 5000000000]))  # tpyc: ok
    # a free function's protocol parameter
    print("free", one(5000000000))  # tpyc: warning(/passed as int \(BigInt\)/)
    # keyword arguments, to a function and to str()
    print("kw", one(x=5000000000), str(object=5000000001))  # tpyc: warning(/literal 5000000000 /) warning(/literal 5000000001 /)
    # a nested call: only the inner literal is an int argument
    print("nested", one(str(5000000002)))  # tpyc: warning(/literal 5000000002 .*passed as int \(BigInt\)/)

def main() -> None:
    shapes()
    part_positions.main()
    part_silent.main()

main()

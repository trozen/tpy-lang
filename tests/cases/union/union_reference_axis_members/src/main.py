# A pointer-variant union whose members are on the reference axis but spell
# DIFFERENT C++ types: `list | bytearray` (std::vector<int32_t> vs
# std::vector<uint8_t>) and `list | Array` (vs std::array). The union binding
# aliases the operand, so mutating through it after the boundary is visible on
# the source -- the members-render-identically case is the error_ sibling.
from tpy import Array, Int32


def grow(u: list[Int32] | bytearray) -> None:
    if isinstance(u, bytearray):
        u.append(33)  # tpyc: ok
    else:
        u.append(9)  # tpyc: ok


def touch(u: list[Int32] | Array[Int32, 2]) -> None:
    if isinstance(u, list):
        u.append(4)
    else:
        u[0] = 7  # tpyc: ok


def main() -> None:
    ba = bytearray(b"ab")
    # The decl under test: a bytearray NAME into a ptr-variant union slot.
    u: list[Int32] | bytearray = ba
    grow(u)
    print(len(ba))

    xs: list[Int32] = [1, 2]
    v: list[Int32] | bytearray = xs
    grow(v)
    print(len(xs))

    a = Array[Int32, 2]()
    ua: list[Int32] | Array[Int32, 2] = a
    touch(ua)
    print(a[0])


main()

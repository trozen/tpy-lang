# The Own[container] return's SOURCE arms key on the same reference axis the
# storage SLOT does, not on a family list: a bare owned Array NAME and a
# REASSIGNED bytearray local (a rebind-slot pointer, returned by deref+move)
# fill the slot exactly as the list flavour beside them already did.
#
# The `Box` leg pins the "returned by move" half as a move: `Box` is @nocopy,
# so the same reassigned-local return with a noncopyable payload is a C++
# compile error if the slot copies. The bytearray leg cannot carry that check
# itself (its elements are UInt8), so it rides the list sibling.
from tpy import Array, Int32, Own
from tplib.box import Box


def fresh_array() -> Own[Array[Int32, 3]]:
    a: Array[Int32, 3] = [1, 2, 3]
    a[0] = 7
    return a                      # tpyc: ok -- the bare owned Array NAME


def grow_bytes(n: Int32) -> Own[bytearray]:
    buf = bytearray(b"ab")
    if n > 2:
        buf = bytearray(b"cde")   # reassigned -> a rebind-slot pointer local
    return buf                    # tpyc: ok -- deref+move out of the slot


def grow_list(n: Int32) -> Own[list[Int32]]:
    xs = [1, 2]
    if n > 2:
        xs = [3, 4, 5]
    return xs                     # the list flavour that already routed


def grow_boxes(n: Int32) -> Own[list[Box[Int32]]]:
    bs: list[Box[Int32]] = [Box(1)]
    if n > 2:
        bs = [Box(2), Box(3)]     # the same rebind slot, @nocopy payload
    return bs                     # tpyc: ok -- a copy here would not compile


def main() -> None:
    a = fresh_array()
    # The caller owns the returned storage, so writing through it is safe and
    # the value it hands back is the one the callee built.
    a[1] = 42
    print(a[0], a[1], len(a))

    b = grow_bytes(4)
    b.append(70)
    print(len(b), b[-1])

    xs = grow_list(4)
    xs.append(9)
    print(len(xs), xs[-1])

    bs = grow_boxes(4)
    bs.append(Box(4))
    print(len(bs))


main()

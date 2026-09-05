# An `Own[...]` parameter over the reference axis -- bytearray, list, and a
# list of a @nocopy element. The callee's buffer is its OWN and is mutable
# there, and the bare NAME read renders the same for all three.
#
# The caller's binding is deliberately not re-read after the call: each
# argument is at its LAST USE, so it MOVES into the callee and there is
# nothing left to observe. (Reading it afterwards would instead make the
# argument a borrow, which the Own slot copies with a warning -- a different
# shape, and the one place this boundary diverges from CPython's aliasing.)
#
# The `Box` leg is what makes the move real rather than assumed: `Box` is
# @nocopy, so a silent copy at this boundary would be a C++ compile error
# instead of an invisible duplicate the output cannot show.
from tpy import Int32, Own
from tplib.box import Box


def consume(b: Own[bytearray]) -> Int32:  # tpyc: warning(/never consumed/)
    b.append(90)                 # the owned buffer is mutable in the callee
    return len(b)                # the bare Own[bytearray] name read


def consume_list(xs: Own[list[Int32]]) -> Int32:  # tpyc: warning(/never consumed/)
    xs.append(3)
    return len(xs)               # the same shape one family over


def consume_boxes(bs: Own[list[Box[Int32]]]) -> Int32:  # tpyc: ok
    # The @nocopy leg: this slot has to MOVE, a copy would not compile. It
    # draws no "never consumed" warning where its two siblings do, because a
    # noncopyable payload leaves the caller nothing to keep either way.
    bs.append(Box(4))
    return len(bs)


def main() -> None:
    ba = bytearray(b"abc")
    print(consume(ba))  # tpyc: ok
    ls: list[Int32] = [1, 2]
    print(consume_list(ls))  # tpyc: ok
    boxes: list[Box[Int32]] = [Box(1)]
    print(consume_boxes(boxes))  # tpyc: ok


main()

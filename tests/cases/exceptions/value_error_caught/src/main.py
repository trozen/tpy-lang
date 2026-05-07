# Runtime ValueError-shape panics are now catchable throws. Exercises the
# representative surfaces: list/bytearray remove-missing, str/bytes empty
# separator, str.index missing substring, bytes value out of range, range
# step==0, slice step==0, file open() invalid mode, float()/int() parse
# errors, time.sleep negative, negative shift count.

import time
from tpy import Int32


def main() -> None:
    # list.remove(x) with x not in list.
    try:
        xs: list[int] = [1, 2, 3]
        xs.remove(99)
    except ValueError as e:
        print("caught:", str(e))

    # list.index(x) -- TPy's "list.index(x): x not in list" diverges from
    # CPython's "<repr(x)> is not in list" until generic value-formatting
    # lands in the runtime; covered by panic_arraylist_index_not_found.

    # bytearray.remove with value not in bytearray.
    try:
        ba: bytearray = bytearray(b"abc")
        ba.remove(200)
    except ValueError as e:
        print("caught:", str(e))

    # bytes value out of range.
    try:
        print(bytes([300]))
    except ValueError as e:
        print("caught:", str(e))

    # str.split with empty separator.
    try:
        "abc".split("")
    except ValueError as e:
        print("caught:", str(e))

    # str.index with substring not found.
    try:
        print("hello".index("xyz"))
    except ValueError as e:
        print("caught:", str(e))

    # str.rindex with substring not found.
    try:
        print("hello".rindex("xyz"))
    except ValueError as e:
        print("caught:", str(e))

    # range() with step == 0.
    try:
        print(list(range(0, 10, 0)))
    except ValueError as e:
        print("caught:", str(e))

    # slice step == 0.
    try:
        zs: list[int] = [1, 2, 3, 4]
        print(zs[::0])
    except ValueError as e:
        print("caught:", str(e))

    # open() with invalid mode.
    try:
        f = open("/tmp/anything", "z")
    except ValueError as e:
        print("caught:", str(e))

    # float() parse error.
    try:
        print(float("not_a_number"))
    except ValueError as e:
        print("caught:", str(e))

    # int() parse error.
    try:
        print(int("not_a_number"))
    except ValueError as e:
        print("caught:", str(e))

    # time.sleep with negative argument.
    try:
        time.sleep(-1.0)
    except ValueError as e:
        print("caught:", str(e))

    # Negative shift count (fixed-int).
    try:
        a: Int32 = Int32(1)
        b: Int32 = Int32(-1)
        print(a << b)
    except ValueError as e:
        print("caught:", str(e))

    # Negative shift count (BigInt).
    try:
        c: int = 1
        print(c << -1)
    except ValueError as e:
        print("caught:", str(e))


main()

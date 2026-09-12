# A builtin-container stub method whose result is a storage-form CONTAINER,
# called as a bare statement with the result thrown away. Python evaluates the
# call and drops the value; the emitted C++ is the same bare stub call, so the
# receiver must be untouched by it.
#
# `xs.append(4)` below is deliberate as well as load-bearing: it keeps the
# literal a `list`. An UNMUTATED literal resolves to `Array[T, N]`, whose
# `.copy()` mis-dispatches to `::tpy::list_copy` and fails the C++ build
# (BUGS.md#array-copy-dispatches-to-list-copy) -- an Array-resolved receiver
# is that entry's shape, not this case's.
from tpy import int32


def main() -> None:
    xs = [3, 1]
    ss = {1, 2}
    other = {2, 3}
    ba = bytearray(b" ab ")
    xs.copy()  # tpyc: ok
    ss.copy()  # tpyc: ok
    ss.union(other)  # tpyc: ok
    ss.difference(other)  # tpyc: ok
    ba.strip()  # tpyc: ok
    ba.upper()  # tpyc: ok
    # Each receiver is mutated AFTER the discarded call and read back, so a
    # discarded result that had consumed or reseated its receiver would show.
    xs.append(4)
    ss.add(9)
    ba.append(120)
    print(xs)
    print(len(ss))
    print(2 in ss)
    print(9 in ss)
    print(ba)


main()

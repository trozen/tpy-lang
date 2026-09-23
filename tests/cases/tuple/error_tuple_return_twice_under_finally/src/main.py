# A name returned at two owning tuple slots under a finally is not deferred:
# the explicit-copy rule applies exactly as it does without the finally.
# TO BE FIXED: the scalar twin WARNS and copies here; this error becomes that
# warning (BUGS.md#borrowed-tuple-at-own-call-arg, plan unit U3 D1).
from tpy import Own, int32


def twice() -> tuple[Own[list[int32]], Own[list[int32]]]:
    ys = [1, 2, 3]
    try:
        return (ys, ys)  # tpyc: error(/Cannot return borrowed value as tuple element 0/)
    finally:
        ys.append(4)


def main() -> None:
    a, b = twice()
    print(len(a), len(b))


main()

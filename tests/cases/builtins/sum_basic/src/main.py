# sum() builtin with different numeric types and start values
from tpy import int32, int64

def main() -> None:
    # int32
    vals = [1, 2, 3, 4, 5]
    print(sum(vals))
    print(sum(vals, 100))

    # int64
    big: list[int64] = [1000000000, 2000000000, 3000000000]
    print(sum(big))
    # The start literal takes the elements' int64.
    s64 = sum(big, 5)  # tpyc: type(int64)
    print(s64)
    # A wider start widens the int32 elements to its int64.
    acc = sum(vals, int64(2147483647))  # tpyc: type(int64)
    print(acc)

    # float
    floats = [1.5, 2.5, 3.0]
    print(sum(floats))
    print(sum(floats, 10.0))

    # empty
    empty: list[int32] = []
    print(sum(empty))
    print(sum(empty, 42))
    # No element type: the start literal and the default int decide.
    s0 = sum([], 5)  # tpyc: type(int32)
    print(s0)

    # dict values: a typed and an inferred dict
    d64: dict[str, int64] = {"a": 1, "b": 2}
    sd64 = sum(d64.values())  # tpyc: type(int64)
    di = {"a": 1}
    sdi = sum(di.values())  # tpyc: type(int32)
    print(sd64, sdi)

    # generator expression
    print(sum(x * x for x in [1, 2, 3, 4]))

main()

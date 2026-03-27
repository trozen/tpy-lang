# sum() builtin with different numeric types and start values
from tpy import Int32, Int64

def main() -> None:
    # Int32
    vals = [1, 2, 3, 4, 5]
    print(sum(vals))
    print(sum(vals, 100))

    # Int64
    big: list[Int64] = [1000000000, 2000000000, 3000000000]
    print(sum(big))

    # float
    floats = [1.5, 2.5, 3.0]
    print(sum(floats))
    print(sum(floats, 10.0))

    # empty
    empty: list[Int32] = []
    print(sum(empty))
    print(sum(empty, 42))

    # generator expression
    print(sum(x * x for x in [1, 2, 3, 4]))

main()

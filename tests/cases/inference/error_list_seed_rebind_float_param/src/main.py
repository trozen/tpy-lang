# An int list local rebound to a float list value (not a literal) is refused:
# a local has one numeric type, its elements included.

def rebind(floats: list[float]) -> None:
    xs = [1, 2]
    xs = floats  # tpyc: error(/'xs' is bound to int elements at line 5 and to float elements here.*write 1\.0 and 2\.0 instead of 1 and 2/)
    print(xs)

rebind([2.5])

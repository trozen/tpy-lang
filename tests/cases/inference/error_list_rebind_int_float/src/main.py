# An int list local rebound to a float list literal is refused like a scalar
# local: a local has one numeric type, its elements included.

def lists() -> None:
    xs = [1, 2]
    xs = [2.5]  # tpyc: error(/'xs' is bound to int elements at line 5 and to float elements here.*write 1\.0 and 2\.0 instead of 1 and 2/)
    print(xs)

lists()

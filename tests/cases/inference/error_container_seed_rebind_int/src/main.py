# A container local seeded from a float list and rebound to an int literal list is
# refused: the literal's own elements are ints, whatever the local's type suggests.

def rebind(xs: list[float]) -> None:
    ys = xs
    ys = [1]  # tpyc: error(/'ys' is bound to float elements at line 5 and to int elements here.*write 1\.0 instead of 1/)
    print(ys)

rebind([1.5])

# A float Array local rebound to a literal of ints is refused: the Array type
# the local inferred hints the literal but does not convert its ints.
from tpy import Array


def rebind(a: Array[float, 2]) -> None:
    b = a
    b = [1, 2]  # tpyc: error(/'b' is bound to float elements at line 7 and to int elements here.*write 1\.0 and 2\.0 instead of 1 and 2/)
    print(b[0])


rebind([1.5, 2.5])

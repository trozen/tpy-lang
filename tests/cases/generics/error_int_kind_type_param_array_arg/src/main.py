# An INT-kind type param (`[N: int]`) is a template VALUE param: passing the
# sized array at the open slot has no arm, so `head(a)` rejects -- and with the
# call gone the body is never instantiated. An int-kind body over a sized array
# is pinned by tests/cases/generics/generic_int_param_array (the class form).
from tpy import Array, Int32


def head[N: int](a: Array[Int32, N]) -> Int32:
    return a[0]


def main() -> None:
    a: Array[Int32, 3] = Array[Int32, 3]()
    a[0] = 4
    print(head(a))  # tpyc: error(/call\.generic_arg_slot/)


main()

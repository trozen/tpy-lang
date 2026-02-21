# Passing a type not in the union is a type error
from tpy import Int32


def foo(x: Int32 | str) -> None:
    pass


foo(True)  # tpyc: error(/Type mismatch/)

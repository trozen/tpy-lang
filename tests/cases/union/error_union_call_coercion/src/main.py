# Passing a type not in the union is a type error
from tpy import int32


def foo(x: int32 | str) -> None:
    pass


foo(True)  # tpyc: error(/Type mismatch/)

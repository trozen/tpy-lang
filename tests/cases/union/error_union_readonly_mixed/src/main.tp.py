# Mixing readonly and non-readonly types in a union is an error
from tpy import Int32, readonly


def foo(x: readonly[list[Int32]] | str) -> None:  # tpyc: error(/Cannot mix readonly/)
    pass

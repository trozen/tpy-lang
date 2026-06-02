# Returning a wrapper-shape function result through an Optional[Wrapper]
# pointer-repr slot would emit `&(call())` -- ill-formed C++. Sema
# rejects the pattern and asks for `Own[Optional[wrapper]]` (see the
# `generic_recursive_own_optional_return` happy-path test).
from typing import Optional
from tpy import Int32, Own

type Tree[T] = T | list[Tree[T]]


def build() -> Own[Tree[Int32]]:
    return 7


def g() -> Optional[Tree[Int32]]:
    return build()  # tpyc: error(/returned pointer would dangle/)


def main() -> None:
    g()


main()

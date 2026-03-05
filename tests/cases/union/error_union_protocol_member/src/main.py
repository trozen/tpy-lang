# Mixing protocol and concrete types in a union is an error
from tpy import Int32
from typing import Sized


def foo(x: Sized | Int32) -> None:  # tpyc: error(/Cannot mix protocol/)
    pass

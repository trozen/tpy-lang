# Protocol types cannot be used as union members
from tpy import Int32
from typing import Sized


def foo(x: Sized | Int32) -> None:  # tpyc: error(/Protocol type.*cannot be used as a union member/)
    pass

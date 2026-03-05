# Error: mixing protocol and concrete types in a union
from typing import Sized

def func(items: Sized | int) -> None:  # tpyc: error(/Cannot mix protocol/)
    pass

# Literal[] with non-string argument should produce a parse error
from typing import Literal, overload


@overload
def process(mode: Literal[42]) -> None: ...  # tpyc: error(/Literal currently only supports string/)

def process(mode: str) -> None:
    pass

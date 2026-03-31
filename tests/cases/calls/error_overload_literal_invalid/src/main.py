# Literal[] with unsupported argument types should produce a parse error
from typing import Literal, overload


@overload
def process(mode: Literal[3.14]) -> None: ...  # tpyc: error(/Literal supports string, int, and bool/)

def process(mode: str) -> None:
    pass

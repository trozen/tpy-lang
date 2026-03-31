# Literal cannot mix value types (bool + int)
from typing import Literal, overload


@overload
def process(mode: Literal[True, 1]) -> None: ...  # tpyc: error(/Literal cannot mix value types/)

def process(mode: str) -> None:
    pass

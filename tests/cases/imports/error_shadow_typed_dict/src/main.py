# TypedDict base shadowed by local def -- not recognized as TypedDict
from typing import TypedDict

def TypedDict() -> int:
    return 0

class Point(TypedDict):  # tpyc: error(/TypedDict/)
    x: int
    y: int

# error: class pattern must match inner type of Optional[Record]
from typing import Optional
from dataclasses import dataclass
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

@dataclass
class Rect:
    w: int32
    h: int32

def check(p: Optional[Point]) -> str:
    match p:
        case None:
            return "none"
        case Rect(w=0):  # tpyc: error(/does not match subject type/)
            return "rect"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()

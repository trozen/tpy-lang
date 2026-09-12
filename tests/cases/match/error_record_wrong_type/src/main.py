# error: class pattern must match subject type on concrete record
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

def describe(p: Point) -> str:
    match p:
        case Rect(w=0):  # tpyc: error(/does not match subject type/)
            return "rect"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()

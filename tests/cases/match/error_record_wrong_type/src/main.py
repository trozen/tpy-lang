# error: class pattern must match subject type on concrete record
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Point:
    x: Int32
    y: Int32

@dataclass
class Rect:
    w: Int32
    h: Int32

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

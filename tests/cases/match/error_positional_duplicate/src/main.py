# error: field bound both positionally and by keyword
from dataclasses import dataclass

@dataclass
class Pair:
    x: float
    y: float

@dataclass
class Other:
    v: float

def describe(s: Pair | Other) -> str:
    match s:
        case Pair(a, x=b):  # tpyc: error(/bound both positionally and by keyword/)
            return "pair"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()

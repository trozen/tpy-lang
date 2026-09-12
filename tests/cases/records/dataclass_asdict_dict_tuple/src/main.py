# Test asdict()/astuple() recursion into dict values and tuple elements
from dataclasses import dataclass, asdict, astuple
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

# dict[str, Dataclass] -- values recursed
@dataclass
class DictOfDC:
    items: dict[str, Point]

# tuple[Dataclass, scalar] -- DC elements recursed
@dataclass
class TupleOfDC:
    pair: tuple[Point, int32]

# tuple[Dataclass, Dataclass] -- both recursed
@dataclass
class TupleAllDC:
    pair: tuple[Point, Point]

def main() -> None:
    # asdict: dict values recursed into nested dicts
    d = DictOfDC({"origin": Point(int32(0), int32(0)), "end": Point(int32(1), int32(2))})
    print(asdict(d))

    # asdict: tuple with DC element
    t = TupleOfDC((Point(int32(1), int32(2)), int32(42)))
    print(asdict(t))

    # asdict: tuple with all DC elements
    t2 = TupleAllDC((Point(int32(1), int32(2)), Point(int32(3), int32(4))))
    print(asdict(t2))

    # astuple: dict values recursed into tuples
    d2 = DictOfDC({"origin": Point(int32(0), int32(0))})
    print(astuple(d2))

    # astuple: tuple with DC element
    t3 = TupleOfDC((Point(int32(1), int32(2)), int32(42)))
    print(astuple(t3))

main()

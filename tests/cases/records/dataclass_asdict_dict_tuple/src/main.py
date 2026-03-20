# Test asdict()/astuple() recursion into dict values and tuple elements
from dataclasses import dataclass, asdict, astuple
from tpy import Int32

@dataclass
class Point:
    x: Int32
    y: Int32

# dict[str, Dataclass] -- values recursed
@dataclass
class DictOfDC:
    items: dict[str, Point]

# tuple[Dataclass, scalar] -- DC elements recursed
@dataclass
class TupleOfDC:
    pair: tuple[Point, Int32]

# tuple[Dataclass, Dataclass] -- both recursed
@dataclass
class TupleAllDC:
    pair: tuple[Point, Point]

def main() -> None:
    # asdict: dict values recursed into nested dicts
    d = DictOfDC({"origin": Point(Int32(0), Int32(0)), "end": Point(Int32(1), Int32(2))})
    print(asdict(d))

    # asdict: tuple with DC element
    t = TupleOfDC((Point(Int32(1), Int32(2)), Int32(42)))
    print(asdict(t))

    # asdict: tuple with all DC elements
    t2 = TupleAllDC((Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))))
    print(asdict(t2))

    # astuple: dict values recursed into tuples
    d2 = DictOfDC({"origin": Point(Int32(0), Int32(0))})
    print(astuple(d2))

    # astuple: tuple with DC element
    t3 = TupleOfDC((Point(Int32(1), Int32(2)), Int32(42)))
    print(astuple(t3))

main()

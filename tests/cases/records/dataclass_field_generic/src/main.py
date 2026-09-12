# field(default_factory=...) with generic user type
from dataclasses import dataclass, field
from tpy import int32

class Pair[T]:
    first: T
    second: T
    def __init__(self, first: T = T(), second: T = T()) -> None:
        self.first = first
        self.second = second
    def __eq__(self, other: Pair[T]) -> bool:
        return self.first == other.first and self.second == other.second

@dataclass
class Wrapper:
    name: str
    pair: Pair[int32] = field(default_factory=Pair)

def main() -> None:
    w = Wrapper("test")
    print(w.pair.first)
    print(w.pair.second)
    w2 = Wrapper("test2", Pair(10, 20))
    print(w2.pair.first)
    print(w2.pair.second)

main()

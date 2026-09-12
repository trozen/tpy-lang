# Readonly method with mutable output parameter.
# The method is const (doesn't mutate self) but the writer param IS mutated.
from tpy import int32

class Writer:
    _parts: list[str]

    def __init__(self) -> None:
        self._parts = []

    def write(self, s: str) -> None:
        self._parts.append(s)

    def result(self) -> str:
        return ",".join(self._parts)

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def encode(self, writer: Writer) -> None:
        writer.write(str(self.x))
        writer.write(str(self.y))

def main() -> None:
    p = Point(3, 7)
    w = Writer()
    p.encode(w)
    print(w.result())

main()

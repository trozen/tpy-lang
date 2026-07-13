# Pending container/view locals passed to non-variadic METHOD params resolve
# against the param type (list not Array; empty-[] inference; element widening).
from tpy import Int32, Int64, Span
from tplib import Box


class Sink:
    def take(self, xs: list[Int32]) -> None:
        xs.append(99)

    def widen(self, xs: list[Int64]) -> None:
        xs.append(1000)

    def first(self, xs: Span[Int32]) -> Int32:
        return xs[0]

    def add(self, d: dict[str, Int32]) -> None:
        d["z"] = 100

    def grow(self, s: set[Int32]) -> None:
        s.add(50)

    def greet(self, name: str) -> str:
        return name + "!"


def main() -> None:
    sink = Sink()

    data = [9]
    sink.take(data)
    print(data)

    e = []
    sink.take(e)
    print(e)

    span_src = [1, 2, 3]  # tpyc: type(/Array\[Int32, 3\]/)
    print(sink.first(span_src))

    w = [4]
    sink.widen(w)
    print(w)

    d = {"a": 1}
    sink.add(d)
    print(d["a"], d["z"])

    s = {1, 2}
    sink.grow(s)
    print(len(s), 50 in s)

    word = "hi"
    print(sink.greet(word))

    b = Box([1, 2])
    other = [7]
    b.set(other)
    print(b.get())


main()

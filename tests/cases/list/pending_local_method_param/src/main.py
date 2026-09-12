# Pending container/view locals passed to non-variadic METHOD params resolve
# against the param type (list not Array; empty-[] inference; element widening).
from tpy import int32, int64, Span
from tplib import Box


class Sink:
    def take(self, xs: list[int32]) -> None:
        xs.append(99)

    def widen(self, xs: list[int64]) -> None:
        xs.append(1000)

    def first(self, xs: Span[int32]) -> int32:
        return xs[0]

    def add(self, d: dict[str, int32]) -> None:
        d["z"] = 100

    def grow(self, s: set[int32]) -> None:
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

    span_src = [1, 2, 3]  # tpyc: type(/Array\[int32, 3\]/)
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

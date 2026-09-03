# A nested def taking a CONTAINER parameter (list / dict / set / bytearray).
# The lambda binds `T&`, so a mutation inside the closure must be visible to
# the caller -- a silent copy would print the pre-call values.
from tpy import Int32


def main() -> None:
    def push(xs: list[Int32]) -> None:
        xs.append(9)  # mutates the CALLER's list

    def bump(d: dict[str, Int32]) -> None:
        d["n"] = d["n"] + 1

    def mark(s: set[Int32]) -> None:
        s.add(7)

    def stamp(b: bytearray) -> None:
        b.append(65)

    def total(xs: list[Int32]) -> Int32:
        s = 0
        for x in xs:
            s += x
        return s

    data = [1, 2]
    push(data)  # tpyc: ok
    counts = {"n": 1}
    bump(counts)  # tpyc: ok
    seen = {1}
    mark(seen)  # tpyc: ok
    buf = bytearray()
    stamp(buf)  # tpyc: ok
    print(len(data), data[2], total(data))
    print(counts["n"], len(seen), len(buf))


main()

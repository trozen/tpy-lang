# Read-only iteration of items()/values() on a readonly dict compiles
# cleanly (const view, readonly elements) -- inverse of the mutation
# rejection cases.
from tpy import Int32
from tpy import readonly


def total(d: readonly[dict[str, list[Int32]]]) -> Int32:
    n = 0
    for k, v in d.items():  # tpyc: ok
        n = n + len(v)
    for v in d.values():  # tpyc: ok
        n = n + v[0]
    return n


def main():
    d: dict[str, list[Int32]] = {}
    d["a"] = [1, 2]
    d["b"] = [3]
    print(total(d))


main()

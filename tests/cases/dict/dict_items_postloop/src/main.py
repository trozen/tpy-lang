# Loop vars leak past the loop (Python scoping): the items()-unpacked
# value var aliases the LAST element after the loop, and mutating
# through it reaches the container. Same for list-of-tuples unpacks.
from tpy import Int32


def main():
    d: dict[str, list[Int32]] = {}
    d["a"] = [1, 2, 3]
    d["b"] = [4, 5]
    for k, v in d.items():
        pass
    print(k, len(v))
    v.append(6)
    print(d["b"])

    pairs: list[tuple[Int32, list[Int32]]] = [(1, [10]), (2, [20])]
    for n, xs in pairs:
        pass
    xs.append(30)
    print(n, pairs[1][1])


main()

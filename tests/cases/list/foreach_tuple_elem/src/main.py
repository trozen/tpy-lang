# Single-target for-each over a value-tuple element: a list[tuple[...]] element
# and a d.items() key/value pair bind the whole tuple as the loop var, read via
# std::get. Distinct from the tuple-UNPACK loop (for k, v in ...), which
# desugars to a per-target decl list.
def dump_pairs(xs: list[tuple[int, int]]) -> None:
    for t in xs:
        print(t[0], t[1])

def dump_items(d: dict[int, int]) -> None:
    for kv in d.items():
        print(kv[0], kv[1])

def sum_first(xs: list[tuple[int, int]]) -> int:
    acc = 0
    for t in xs:
        acc = acc + t[0]
    return acc

def main() -> None:
    pairs = [(1, 2), (3, 4)]
    dump_pairs(pairs)
    dump_items({5: 6, 7: 8})
    print(sum_first(pairs))

main()

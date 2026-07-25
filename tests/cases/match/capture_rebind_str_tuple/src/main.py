# Sibling coverage for the rebound-capture hoist across the value-type family:
# a `str` capture rebound in a nested block and a tuple-of-values capture
# rebound at arm top level both assign their own local, leaving the matched
# object untouched (CPython rebinds a fresh local).
class Box:
    label: str
    pair: tuple[int, int]
    def __init__(self, label: str, pair: tuple[int, int]) -> None:
        self.label = label
        self.pair = pair


def relabel(b: Box) -> str:
    match b:
        case Box(label=s):
            if len(s) > 0:
                s = "x:" + s   # tpyc: ok
            return s           # "x:hi"
    return "?"


def repair(b: Box) -> int:
    match b:
        case Box(pair=t):
            t = (7, 8)         # tpyc: ok
            return t[0] + t[1]  # 15
    return -1


def main() -> None:
    b = Box("hi", (1, 2))
    print(relabel(b), b.label)            # x:hi hi -- field untouched
    print(repair(b), b.pair[0], b.pair[1])  # 15 1 2 -- field untouched


main()

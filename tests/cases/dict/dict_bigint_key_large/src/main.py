# BigInt-keyed dict subscripts must not narrow the key: keys beyond int32
# range store/load/delete/aug-assign exactly as in CPython (the map's key
# type IS BigInt).
def bump(d: dict[int, int], k: int) -> None:
    d[k] += 5


def main():
    d: dict[int, str] = {}
    k: int = 1099511627776  # 2**40
    d[k] = "big"
    print(d[k])
    print(k in d)
    del d[k]
    print(len(d))
    d[1125899906842624] = "lit"  # 2**50: a literal key beyond int32
    print(d[1125899906842624])
    counts: dict[int, int] = {}
    counts[k] = 1
    bump(counts, k)
    print(counts[k])
    print(counts.pop(k))


main()

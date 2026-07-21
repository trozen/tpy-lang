# Int64-keyed dict: a BigInt key narrows to the DECLARED width (int64), so
# keys beyond int32 but within int64 range work like CPython -- across
# store, load, aug-assign, and del.
from tpy import Int32, Int64


def main():
    d: dict[Int64, str] = {}
    k: int = 1099511627776  # 2**40
    d[k] = "big"
    print(d[k])
    del d[k]
    print(len(d))
    counts: dict[Int64, Int32] = {}
    counts[k] = 1
    counts[k] += 5
    print(counts[k])


main()

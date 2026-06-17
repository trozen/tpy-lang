# collections.Counter v1: construct/[]/len/in/total, most_common(n) (n=0, n>len,
# ties), update/subtract (incl. to negative). cpy phase checks real-Counter parity.
from collections import Counter


def main() -> None:
    words: list[str] = ["a", "b", "a", "c", "a", "b", "c"]
    c = Counter(words)
    print(c["a"], c["b"], c["z"])      # 3 2 0  (z missing -> 0, no insert)
    print(len(c), "a" in c, "z" in c)  # 3 True False
    print(c.total())                   # 7

    # b and c both have count 2 -> tie keeps first-seen (insertion) order: b, c
    for k, n in c.most_common(3):
        print(k, n)                    # a 3 / b 2 / c 2

    # n bounds: 0 -> empty, n > len -> all
    print(len(c.most_common(0)))       # 0
    print(len(c.most_common(99)))      # 3

    # update adds; subtract removes and keeps zero/negative counts
    more = Counter(["a", "x", "x"])
    c.update(more)
    print(c["a"], c["x"])              # 4 2
    c.subtract(more)
    print(c["a"], c["x"])              # 3 0
    c.subtract(more)
    print(c["x"], c.total())           # -2 ... (negative kept, counted in total)

    # int keys + explicit count assignment
    nums = Counter([1, 1, 2])
    nums[3] = 5
    print(nums[1], nums[2], nums[3])   # 2 1 5

    empty: Counter[str] = Counter()
    print(len(empty), empty["nope"])   # 0 0


main()

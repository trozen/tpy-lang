# A user __getitem__/__setitem__ whose key param is int (BigInt): the key
# passes through unnarrowed, so keys beyond int32 range work like CPython.
from tpy import Int32


class SparseCounter:
    data: dict[int, Int32]

    def __init__(self):
        self.data = {}

    def __getitem__(self, key: int) -> Int32:
        return self.data.get(key, 0)

    def __setitem__(self, key: int, value: Int32) -> None:
        self.data[key] = value


def main():
    c = SparseCounter()
    k: int = 17592186044416  # 2**44
    c[k] = 7
    print(c[k])
    print(c[k + 1])


main()

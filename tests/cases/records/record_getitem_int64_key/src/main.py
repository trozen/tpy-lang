# A user __getitem__/__setitem__ whose key param is a FIXED int (int64): a
# BigInt key narrows to the declared width, so keys beyond int32 but within
# int64 range work like CPython.
from tpy import int32, int64


class WidePages:
    data: dict[int64, int32]

    def __init__(self):
        self.data = {}

    def __getitem__(self, key: int64) -> int32:
        return self.data.get(key, 0)

    def __setitem__(self, key: int64, value: int32) -> None:
        self.data[key] = value


def main():
    p = WidePages()
    k: int = 17592186044416  # 2**44
    p[k] = 9
    print(p[k])
    print(p[k + 1])


main()

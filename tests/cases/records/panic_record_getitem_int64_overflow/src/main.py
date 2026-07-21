# A BigInt key beyond a user __setitem__'s declared Int64 key width takes
# the checked narrow and panics (CPython accepts any int; the Int64 param
# declares the key domain; panic_ cases skip the cpy phase).
from tpy import Int32, Int64


class WidePages:
    data: dict[Int64, Int32]

    def __init__(self):
        self.data = {}

    def __getitem__(self, key: Int64) -> Int32:
        return self.data.get(key, 0)

    def __setitem__(self, key: Int64, value: Int32) -> None:
        self.data[key] = value


def main():
    p = WidePages()
    k: int = 1180591620717411303424  # 2**70
    p[k] = 9


main()

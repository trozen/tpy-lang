# A BigInt key beyond a user __setitem__'s declared int64 key width takes
# the checked narrow and panics (CPython accepts any int; the int64 param
# declares the key domain; panic_ cases skip the cpy phase).
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
    k: int = 1180591620717411303424  # 2**70
    p[k] = 9


main()

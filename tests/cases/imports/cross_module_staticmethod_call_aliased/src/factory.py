from tpy import int32, Own


class Counter:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

    @staticmethod
    def make(v: int32) -> Own["Counter"]:
        return Counter(v)

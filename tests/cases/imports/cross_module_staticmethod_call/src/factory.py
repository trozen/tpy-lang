from tpy import Int32, Own


class Counter:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v

    @staticmethod
    def make(v: Int32) -> Own["Counter"]:
        return Counter(v)

    @staticmethod
    def zero() -> Own["Counter"]:
        return Counter(Int32(0))

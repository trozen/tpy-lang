from tpy import int32, readonly


class Ops:
    @staticmethod
    @readonly
    def plus_one(x: int32) -> int32:
        return x + 1


@readonly
def add_one(x: int32) -> int32:
    return Ops.plus_one(x)  # tpyc: ok


print(add_one(3))

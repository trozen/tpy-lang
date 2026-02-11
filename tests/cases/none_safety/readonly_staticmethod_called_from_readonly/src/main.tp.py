from tpy import Int32, readonly


class Ops:
    @staticmethod
    @readonly
    def plus_one(x: Int32) -> Int32:
        return x + 1


@readonly
def add_one(x: Int32) -> Int32:
    return Ops.plus_one(x)  # tpyc: ok


print(add_one(3))

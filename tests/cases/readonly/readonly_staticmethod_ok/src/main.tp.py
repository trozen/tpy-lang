from tpy import Int32, readonly


class Ops:
    @staticmethod
    @readonly
    def plus_one(x: Int32) -> Int32:
        return x + 1


print(Ops.plus_one(3))

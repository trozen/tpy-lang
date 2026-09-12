from tpy import int32, readonly


class Ops:
    @staticmethod
    @readonly
    def plus_one(x: int32) -> int32:
        return x + 1


print(Ops.plus_one(3))

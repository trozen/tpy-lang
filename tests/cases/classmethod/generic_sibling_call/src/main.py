# A classmethod calling a sibling method that carries its OWN type parameters
# through `cls`: the callee spelling must name the resolved record, since `cls`
# is not a C++ name (the generic-static path spells class and method type args
# separately from the plain one).
from tpy import Int32


class Util:
    @staticmethod
    def pick[T](a: T, b: T) -> T:
        if a > b:
            return a
        return b

    @classmethod
    def larger(cls, a: Int32, b: Int32) -> Int32:
        return cls.pick(a, b)

    @classmethod
    def larger_float(cls, a: float, b: float) -> float:
        return cls.pick(a, b)


def main() -> None:
    print(Util.larger(3, 7))
    print(Util.larger_float(1.5, 0.5))


main()

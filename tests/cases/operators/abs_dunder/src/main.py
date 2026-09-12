# Regression: abs() dispatches to a user type's __abs__ (the builtin abs
# overloads only covered numeric types; abs(user_type) errored). Mirrors
# CPython's abs() -> x.__abs__().
from tpy import int32, ValueType


class Temp(ValueType):
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __abs__(self) -> "Temp":
        return Temp(self.v if self.v >= 0 else -self.v)


def main() -> None:
    print(abs(Temp(-5)).v)   # 5
    print(abs(Temp(7)).v)    # 7


main()

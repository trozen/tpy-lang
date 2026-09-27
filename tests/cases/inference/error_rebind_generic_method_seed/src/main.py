# A float local rebound to a generic METHOD whose T only the local would pick
# for the argument is refused: first([a]) returns the int a.
from tpy import int32


class M:
    def first[T](self, v: list[T]) -> T:
        return v[0]


def rebind(a: int32) -> None:
    y = 0.5
    y = M().first([a])  # tpyc: error(/'y' is bound to float at line 12 and to int32 here/)
    print(y)


rebind(3)

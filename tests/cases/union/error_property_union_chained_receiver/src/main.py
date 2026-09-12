# A union-returning property reached through a CHAINED receiver: the source
# gate admits bare-name receivers only, because the decl arm's const verdict
# reads it, so `h.canvas.shape` is rejected.
from tpy import int32


class Circle:
    radius: int32

    def __init__(self, radius: int32) -> None:
        self.radius = radius


class Square:
    side: int32

    def __init__(self, side: int32) -> None:
        self.side = side


class Canvas:
    _shape: Circle | Square

    def __init__(self, s: Circle | Square) -> None:
        self._shape = s

    @property
    def shape(self) -> Circle | Square:
        return self._shape


class Holder:
    canvas: Canvas

    def __init__(self, c: Canvas) -> None:
        self.canvas = c


def show(h: Holder) -> None:
    s = h.canvas.shape  # tpyc: error(/decl.ptr_union_source/)
    print(isinstance(s, Circle))


def main() -> None:
    show(Holder(Canvas(Circle(1))))


main()

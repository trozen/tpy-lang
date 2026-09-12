# Bare class-body default `coord = Point()` referring to a same-file
# sibling class. F.3g.6 fix: sema's _infer_field_type_from_default now
# consults parser.registry for same-file records (F.3g.2 populated
# module info there), so the error the user sees is the real one
# ("Default field value must be a constant expression") rather than
# the incidental "Cannot infer type for field 'coord'" that F.3f.2's
# sema-only inference path previously surfaced.
from tpy import int32


class Point:
    x: int32
    y: int32

    def __init__(self) -> None:
        self.x = int32(0)
        self.y = int32(0)


class Shape:
    coord = Point()  # tpyc: error(/Default field value must be a constant expression/)

    def __init__(self) -> None:
        self.coord = Point()


def main() -> None:
    s = Shape()
    print(s.coord.x)


main()

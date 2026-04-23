# Regression: cross-module `Own[T]` return assigned to an inferred local when
# only the module (not `T`) is imported. Codegen used to emit an unqualified
# type (`Circle c = ...`) because `imported_record_qualification` looked
# `Circle` up by bare name in main.py's registry and missed -- Circle isn't
# imported there. Now resolved via qname-aware lookup.
from tpy import Int32
import shapes


def main() -> None:
    c = shapes.make_circle(Int32(7))
    print(c.radius)


main()

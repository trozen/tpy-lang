# A classmethod called from another module, through every receiver spelling:
# a plain import, an aliased import, and a module-qualified name. `cls`
# resolves in the DEFINING module, so the constructor spells that module's
# record while the call site qualifies the receiver.
import shapes
from shapes import Point
from shapes import Point as P


def main() -> None:
    a = Point.origin()
    b = P.at(3)
    c = shapes.Point.at(7)
    print(a.x, b.x, c.x)


main()

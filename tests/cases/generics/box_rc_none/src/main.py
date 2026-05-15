# Box[None] / Rc[None] -- value-bearing TPy wrappers instantiated with
# the unit type. Regression guard for the architectural None lowering:
# T=None at type-arg position must produce a well-formed `Own[T]` param
# in the wrapper's constructor. Conjectured-broken before the fix
# (BUGS.md noted Future[None] and "any other generic with an Own[T]
# method instantiated with T=None likely hits the same wall").
from tplib.rc import Rc
from tplib.box import Box


def main() -> None:
    r = Rc.new(None)
    print("rc constructed")

    b = Box(None)
    print("box constructed")


main()

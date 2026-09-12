# A generic STATIC method called through its module (`mod.Cls.m(args)`): the
# class is qualified through the module namespace and the method's own type
# argument rides the call.
import helpers
from tpy import int32


def use(x: int32, y: int32) -> int32:
    return helpers.Util.second(x, y)


def main() -> None:
    print(use(5, 6), use(9, 2))
    print(helpers.Util.second("a", "b"))


main()

# A readonly name narrowed by isinstance passes to a @readonly dunder's
# parameter through the operator as through the explicit method call: a
# callee readonly by declaration (or as an implicitly readonly dunder) makes
# its parameters readonly at every form; one inferred const does not
# (error_readonly_inferred_dunder_narrowed_arg).
from tpy import int32, readonly


class Item:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    @readonly
    def __contains__(self, x: Item) -> bool:
        return x.v in self.items

    @readonly
    def __add__(self, x: Item) -> int32:
        return self.items[0] + x.v


def f(v: readonly[Item | str], h: Holder) -> None:
    if isinstance(v, Item):
        print("explicit:", h.__contains__(v))  # tpyc: ok
        # the operator forms receive `v` as the explicit call does
        print("in:", v in h)  # tpyc: ok
        print("binop:", h + v)  # tpyc: ok


def main() -> None:
    f(Item(2), Holder())


main()

# Empty list `[]` coerced into a recursive-union alias via an annotated
# local (`x: V = []`) -- exercises the recursive-union empty-literal branch
# independent of json. The list is a reference type, so we mutate it after
# the binding (through a narrowing match) and observe the change, forcing
# the value-vs-reference distinction.
from tpy import Int32

type V = Int32 | list[V]


def main() -> None:
    x: V = []
    match x:
        case list() as items:
            items.append(1)
            items.append(2)
        case _:
            pass
    print(x)


main()

# match arm bodies first-declaring a local that is read after the match:
# the branch-decl hoist predeclares it before the switch. `pick` adds a
# hoisted CAPTURE (one arm assigns the name, the other binds the subject
# to it), so the binding renders as an assignment against the predecl.
from tpy import int32


def route(n: int32) -> int32:
    match n:
        case 0:
            label = 10
        case _:
            label = 20
    return label


def pick(n: int32) -> int32:
    match n:
        case 0:
            seen = 100
        case seen:
            print(seen)
    return seen


def main() -> None:
    print(route(0))
    print(route(7))
    print(pick(0))
    print(pick(5))


main()

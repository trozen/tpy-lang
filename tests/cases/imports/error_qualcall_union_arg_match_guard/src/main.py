# A union rvalue at a module-qualified slot needs the pointer lift's named
# temp, and a match guard has no statement to hoist it into -- so it keeps
# rejecting, exactly like the from-import spelling `code(Dog(4))` there.
import pets
from pets import Dog


def classify(n: int) -> str:
    # the union rvalue in the guard is the reject (reported at the match)
    match n:  # tpyc: error(/call\.arg_shape\.union/)
        case 1 if pets.code(Dog(4)) > 3:
            return "big"
        case _:
            return "other"


def main() -> None:
    print(classify(1))


main()

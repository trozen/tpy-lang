# A macro's `raise m.E(7)` / `raise m.D` resolves `m` through the scope, as the
# written raise does: `m` is errmod here, never another module with an E or D.
import m as unrelated
import errmod as m
from raisemacro import raise_d, raise_e


@raise_e
def with_args() -> None:
    pass


@raise_d
def no_args() -> None:
    pass


def main() -> None:
    print("unrelated", str(unrelated.E(1)), str(unrelated.D()))
    # with arguments: the construction of errmod.E
    try:
        with_args()
    except m.E as e:
        print("with-args", str(e), e.x)
    # without: errmod.D()
    try:
        no_args()
    except m.D as e:
        print("no-args", str(e))


main()

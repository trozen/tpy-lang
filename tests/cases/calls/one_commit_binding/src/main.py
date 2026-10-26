# A module binding commits as a function call does: a keyword argument keeps
# the parameter mutable; an element borrow held across a mutating one warns.
from tpy import int32
import mymod
from mymod import P


def push_kw(xs: list[int32]) -> None:
    # Keyword call to a mutating binding: `xs` stays a mutable parameter.
    mymod.push(xs=xs, v=1)


def push_kw_rev(xs: list[int32]) -> None:
    # Keywords in reverse order bind the same parameters.
    mymod.push(v=2, xs=xs)


def wipe_held() -> None:
    xs = [P(1), P(2)]
    e = xs[0]
    # Read before `wipe`: after it `e` dangles (a use-after-free).
    n = e.n
    # The element borrow `e` is live across the mutating binding.
    mymod.wipe(xs)  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    print("wipe:", n, len(xs))


def main() -> None:
    a = [5]
    push_kw(a)
    push_kw_rev(a)
    print("push:", a)
    wipe_held()


main()

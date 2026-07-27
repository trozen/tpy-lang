# The reference-type half of the block-local reassign fix: a record local first
# declared inside a loop body and rebound after it. Rebinding the local must
# leave the object an earlier alias captured alone (CPython rebinds the name,
# it does not overwrite the object), so each case mutates through one handle
# and reads back through the other.
class Point:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


def escaped_alias() -> int:
    # `saved` aliases the loop-body local, whose storage is hoisted to function
    # scope; the post-loop rebind of `p` must not disturb what `saved` holds.
    # Narrow by construction: `saved` is re-captured every iteration, so it
    # holds the last one either way -- this pins the post-loop rebind only, NOT
    # per-iteration identity. A mid-loop conditional capture still clobbers
    # (see BUGS.md); the hoisted storage is one slot per name, not per binding.
    saved = Point(0)
    for i in range(3):
        p = Point(i)
        saved = p
    p = Point(9)
    return saved.x * 100 + p.x


def mutate_through_alias() -> int:
    # Mutating through the rebound handle must be visible through the alias
    # taken from it -- a silent copy at the rebind would hide the write.
    for i in range(2):
        p = Point(i)
    p = Point(9)
    q = p
    q.x = 55
    return p.x


def in_match_arm(tag: int) -> int:
    match tag:
        case 1:
            p = Point(1)
            print(p.x)
        case _:
            pass
    p = Point(9)
    return p.x


def in_try_body() -> int:
    try:
        p = Point(1)
        print(p.x)
    except ValueError:
        pass
    p = Point(9)
    return p.x


def loop_var_rebind(points: list[Point]) -> int:
    # The one-type-per-local rule is scoped to BODY-declared locals: a loop
    # VARIABLE's type comes from the iterable rather than from the user, so a
    # later assignment binds a fresh local rather than being forced into the
    # element type (CPython rebinds the same name either way).
    for p in points:
        print(p.x)
    p = Point(9)
    return p.x


def main() -> None:
    print(escaped_alias())
    print(mutate_through_alias())
    print(in_match_arm(1))
    print(in_match_arm(2))
    print(in_try_body())
    print(loop_var_rebind([Point(1), Point(2)]))


main()

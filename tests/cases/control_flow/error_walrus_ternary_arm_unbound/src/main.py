# A walrus in one arm of a conditional expression binds its target on that
# arm's path only, so a read after the expression may not be assigned.


def tw(c: bool) -> None:
    z = (m := 1) if c else 0
    # The subject: `m` is bound only when `c` held.
    print("tw", z, m)  # tpyc: error(/variable 'm' may not be assigned at this point/)


tw(False)

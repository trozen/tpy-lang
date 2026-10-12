# A tuple loop variable rebound in the body while a name still refers to an
# element it was given: the loop variable aliases the container's element, so
# the new object needs storage of its own, which a loop variable does not have
# yet (BUGS.md#scalar-loop-var-rebind-rejects) -- refused at the rebind, naming
# the alias, where writing in place would overwrite the container's element.
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def pair(n: int32) -> Own[tuple[Box, Box]]:
    return (Box(n), Box(n + 1))


def loop_var(items: list[tuple[Box, Box]]) -> None:
    for t in items:
        a = t[0]
        t = pair(9)  # tpyc: error(/'a' still refers to the object an element of 't' was bound to, so the object the rebind of 't' creates needs storage of its own/)
        a.n += 100
        print(a.n, t[0].n)


loop_var([(Box(1), Box(2))])

# `match xs[0]:` on a `list[T | None]`: the ELEMENT is storage-form
# `std::optional<T>`, so the subject lifts through optional_to_ptr exactly like
# the record-FIELD source does. The arm binding aliases the element -- the
# sections mutate through it and read the container back to prove that.
from tpy import int32


class Box:
    def __init__(self, val: int32) -> None:
        self.val = val


# free function: the element subject, both arms, mutating through the binding.
def bump(xs: list[Box | None]) -> str:
    match xs[0]:  # tpyc: ok
        case None:
            return "none"
        case Box() as b:
            b.val += 100
            return "box"


# free function: a dict VALUE element reads the same way.
def bump_dict(d: dict[str, Box | None]) -> str:
    match d["k"]:  # tpyc: ok
        case None:
            return "none"
        case Box() as b:
            b.val += 5
            return "box"


class Holder:
    def __init__(self, xs: list[Box | None]) -> None:
        self.xs = xs

    # method: the element subject off a field receiver.
    def touch(self) -> str:
        match self.xs[0]:  # tpyc: ok
            case None:
                return "none"
            case Box() as b:
                b.val += 1
                return "box"


def main() -> None:
    # The read-back guards below warn: an `is not None` test on a SUBSCRIPT
    # does not narrow it (BUGS.md#subscript-is-not-none-not-narrowing), so
    # each deref keeps its runtime null check.
    xs: list[Box | None] = [Box(1), None]
    print("list", bump(xs),
          xs[0].val if xs[0] is not None else -1)  # tpyc: warning(/Potential None access/)
    empty: list[Box | None] = [None]
    print("list-none", bump(empty))
    d: dict[str, Box | None] = {"k": Box(2)}
    print("dict", bump_dict(d),
          d["k"].val if d["k"] is not None else -1)  # tpyc: warning(/Potential None access/)
    h = Holder([Box(3), None])
    print("method", h.touch(),
          h.xs[0].val if h.xs[0] is not None else -1)  # tpyc: warning(/Potential None access/)


main()

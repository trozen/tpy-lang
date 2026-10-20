# A value inside an augmented key that must run once, not known to be new and
# read by nothing that yields a number or a new value (here a view of it), is
# refused; binding it to a name first holds it.
from tpy import StrView


def head(s: str) -> StrView:
    return s[:1]


def main() -> None:
    it = iter(["a", "b"])
    d = {"a": 0, "b": 0}
    # `next(it, "a")` runs once; `head` only views it
    d[head(next(it, "a"))] += 1  # tpyc: error(/a value in the subscript key here is evaluated once but is not known to be a new value, so it cannot be held without a copy; bind it to a name first/)
    print(d)


main()

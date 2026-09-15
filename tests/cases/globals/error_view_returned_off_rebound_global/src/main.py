# A StrView returned off a module global that some function rebinds through
# `global` is rejected: the global is durable storage, so the dangling checks
# pass it, but the rebind frees the buffer the returned view points into.
# Both global spellings reach the rule; this case takes the inferred one (the
# compiler stops at the first error, so the annotated spelling and the
# reference-typed global are covered by the happy case's siblings).
# The rebind is spelled `+=`: an augmented assign reseats a `str` slot exactly
# as `=` does, and the plain `=` spelling stays pinned by
# `error_tuple_member_off_rebound_global` / `error_yield_borrow_off_rebound_global`.
from tpy import StrView

S = "hello world, long enough that no small-string buffer hides the reuse"


def view() -> StrView:
    return S  # tpyc: error(/Cannot return a borrow of module variable 'S'.*'reset' rebinds/)


def reset() -> None:
    global S
    S += "!"


def main() -> None:
    v = view()
    reset()
    print(v)


main()

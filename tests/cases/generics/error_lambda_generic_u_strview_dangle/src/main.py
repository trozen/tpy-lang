# Generic `Fn[[T], U]` with U inferred from a lambda body that constructs
# StrView from a temporary -- the dangling-view check must fire on the
# inferred return type, not skip when the Fn hint's return slot is itself
# an unresolved type parameter.
from tpy import Own, copy, Fn, StrView

def map_one[T, U](f: Fn[[T], U], item: T) -> Own[U]:
    return copy(f(item))

def main() -> None:
    map_one(lambda s: StrView(s + "!"), "hello")  # tpyc: error(/dangling|StrView|local or temporary/)

main()

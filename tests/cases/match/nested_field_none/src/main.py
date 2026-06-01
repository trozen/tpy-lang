# A `None` field test nested inside a record sub-pattern -- exercises the
# `_record_field_conditions` recursion (the None check lands on the inner
# record's field, not the outer subject).
class Inner:
    child: "str | None"
    def __init__(self, child: "str | None") -> None:
        self.child = child

class Outer:
    inner: Inner
    def __init__(self, inner: Inner) -> None:
        self.inner = inner

def describe(o: Outer) -> str:
    match o:
        case Outer(inner=Inner(child=None)):  # tpyc: ok
            return "empty inner"
        case _:
            return "full inner"

def main() -> None:
    print(describe(Outer(Inner(None))))
    print(describe(Outer(Inner("hi"))))

main()

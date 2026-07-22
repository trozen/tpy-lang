# A NARROWED storage-form Optional field widened back to an Optional arg or
# return slot must take the optional_to_ptr lift (aliasing), never `&(field)`.
class Pod:
    x: int

    def __init__(self) -> None:
        self.x = 0


class T:
    o: Pod | None

    def __init__(self) -> None:
        self.o = None


def is_def(o: Pod | None) -> bool:
    return o is not None


def is_def_gen[U](o: U | None) -> bool:
    # Generic sibling: the type param must infer U=Pod from a narrowed field
    # arg (whose analyzed type is Ref-wrapped), not U=Ref[Pod] -> val_or_ref.
    return o is not None


def take(t: T) -> Pod | None:
    if t.o is None:
        return None
    return t.o                   # tpyc: ok


def main() -> None:
    t = T()
    print(is_def(t.o))           # un-narrowed baseline: optional_to_ptr lift
    t.o = Pod()
    assert t.o is not None
    t.o.x = 1
    print(is_def(t.o))           # narrowed arg: same lift
    print(is_def_gen(t.o))       # narrowed arg into a GENERIC optional param
    p = take(t)
    assert p is not None
    p.x = 7                      # mutate through the returned borrow
    print(t.o.x)                 # tpyc: warning(/Potential None access/)


main()

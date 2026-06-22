# A name borrow-captured from a reference-type lvalue match subject (aliases
# the subject, mutation visible) and then reused as a `with ... as` target: the
# capture must alias (b.v == 99) and the with-body sees its own object. The two
# bindings share one C++ declaration whose form must stay consistent with the
# match-arm's borrow binding; a regression here reads garbage for b.v.
class Box:
    def __init__(self, v: int):
        self.v = v
    def __enter__(self) -> "Box":
        return self
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

def main():
    b = Box(1)
    match b:
        case q:
            q.v = 99          # q aliases b -- mutation visible
    print(b.v)                # 99

    with Box(5) as q:         # same name, owned with-as binding (fresh object)
        q.v = 7               # mutates the with object, not b
        print(q.v)            # 7
    print(b.v)                # 99 -- b untouched by the with block

main()

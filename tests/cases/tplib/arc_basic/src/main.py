# Basic Arc[T]: construction, get()/method access, mutation through a clone
# visible to all clones (reference semantics -- Arc is @nocopy, so a silent copy
# would be a compile error), and the Send + Sync classification the spawn path
# relies on. Arc's atomic refcount is exercised under real thread contention by
# tests/cases/threading/arc_spawn.
from tpy import Int32
from tplib.arc import Arc


class State:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def doubled(self) -> Int32:
        return self.x * 2


def main() -> None:
    a = Arc.new(State(42))  # tpyc: is_send(yes) is_sync(yes)
    print(a.get().x)          # 42
    print(a.get().doubled())  # 84

    b = a.clone()
    a.get().x = 7             # mutate through a
    print(b.get().x)          # 7 -- shared across clones
    print(b.get().doubled())  # 14

    # Weak carries the same conditional Send/Sync as Arc.
    w = a.downgrade()        # tpyc: is_send(yes) is_sync(yes)
    u = w.upgrade()
    if u is not None:
        print(u.get().x)     # 7 -- still live (a and b hold strong refs)


main()

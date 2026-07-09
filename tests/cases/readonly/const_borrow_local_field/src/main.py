# A reference-type borrow-local from a field/container-field read off an inferred-const
# receiver must bind `const T&`; a mutating method binding the same local keeps `T&`.
class Inner:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

class Outer:
    inner: Inner
    tags: list[int]
    def __init__(self, x: int) -> None:
        self.inner = Inner(x)
        self.tags = []

    def peek_x(self) -> int:      # inferred const receiver; plain field borrow-local
        r = self.inner
        return r.x

    def tag_count(self) -> int:   # inferred const receiver; container-field borrow-local
        xs = self.tags
        return len(xs)

    def bump(self) -> None:       # inverse: mutates via the borrow-local -> non-const receiver
        r = self.inner
        r.x += 1

def read_only(o: Outer) -> int:   # o inferred `const Outer&`; free-function param face
    r = o.inner
    return r.x

def main() -> None:
    o = Outer(5)
    print(o.peek_x())      # 5
    print(read_only(o))    # 5
    o.tags.append(9)
    print(o.tag_count())   # 1
    o.bump()               # mutate the shared Inner through the mutable borrow-local
    print(o.peek_x())      # 6 -- the readonly reader aliases the shared object, not a copy

main()

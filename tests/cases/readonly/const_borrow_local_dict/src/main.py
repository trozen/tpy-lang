# A reference-type borrow-local from a const dict `__getitem__`/`.get()` off an inferred-const
# receiver must bind `const T&`/`const T*`; a mutating method keeps the mutable forms.
class Cookie:
    value: int
    def __init__(self, v: int) -> None:
        self.value = v

class Jar:
    store: dict[str, Cookie]
    def __init__(self) -> None:
        self.store = {}

    def add(self, name: str, v: int) -> None:
        self.store[name] = Cookie(v)

    def peek(self, name: str) -> int:       # inferred const receiver; subscript-on-field
        c = self.store[name]
        return c.value

    def peek_get(self, name: str) -> int:   # inferred const receiver; `.get()` -> const Cookie*
        c = self.store.get(name)
        if c is not None:
            return c.value
        return -1

    def rename(self, name: str) -> None:    # inverse: mutates via the borrow-local -> non-const
        c = self.store[name]
        c.value = 99

def main() -> None:
    j = Jar()
    j.add("a", 3)
    print(j.peek("a"))       # 3
    print(j.peek_get("a"))   # 3
    print(j.peek_get("z"))   # -1
    j.rename("a")            # mutate the shared Cookie through the mutable borrow-local
    print(j.peek("a"))       # 99 -- the readonly reader aliases the shared object, not a copy

main()

# D16 Phase 1: child class inherits __getattr__ from parent via MRO.
class Parent:
    _store: dict[str, str]

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    def __getattr__(self, name: str) -> str:
        return self._store[name]

class Child(Parent):
    def __init__(self, store: dict[str, str]) -> None:
        super().__init__(store)

def main() -> None:
    c = Child({"key": "value"})
    print(c.key)

main()

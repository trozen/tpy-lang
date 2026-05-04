# D16 Phase 5: with multiple inheritance, the first ancestor in MRO declaring
# __getattr__ wins. Mirrors how method dispatch works under D22.
class WithGetattr:
    _store: dict[str, str]

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    def __getattr__(self, name: str) -> str:
        return self._store[name]

class Mixin:
    def helper(self) -> str:
        return "mixin"

class Combined(WithGetattr, Mixin):
    def __init__(self, store: dict[str, str]) -> None:
        super().__init__(store)

def main() -> None:
    c = Combined({"key": "value"})
    print(c.key)
    print(c.helper())

main()

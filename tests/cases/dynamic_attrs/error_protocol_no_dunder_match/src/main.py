# D16 Phase 1: __getattr__ does NOT structurally satisfy a protocol method;
# only declared methods count for static protocol conformance.
from typing import Protocol
from typing import Any

class Greeter(Protocol):
    def greet(self) -> str: ...

class DynBag:
    _store: dict[str, str]

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    def __getattr__(self, name: str) -> str:
        return self._store.get(name, "")

def call_greet(g: Greeter) -> None:
    print(g.greet())

def main() -> None:
    b = DynBag({"x": "hi"})
    call_greet(b)  # tpyc: error(/Greeter|protocol|conform/)

main()

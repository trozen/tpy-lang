# D16 Phase 1: __getattr__ -> str (concrete type) needs no narrowing at use sites.
class Headers:
    _store: dict[str, str]

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    def __getattr__(self, name: str) -> str:
        return self._store[name]

def main() -> None:
    h = Headers({"content_type": "application/json", "host": "example.com"})
    print(h.content_type.upper())
    print(h.host)

main()

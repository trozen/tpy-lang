# dict[str, Any] read-out: indexing yields Any; assignment to a typed
# local triggers auto-coerce.

from typing import Any


def main() -> None:
    cfg: dict[str, Any] = {
        "host": "localhost",
        "port": 8080,
    }
    host: str = cfg["host"]
    port: int = cfg["port"]
    print(host)
    print(port)


main()

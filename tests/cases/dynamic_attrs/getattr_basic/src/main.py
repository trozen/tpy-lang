# D16 Phase 1: __getattr__ -> Any routes undeclared attribute reads through the dunder.
from tpy import int32
from typing import Any, cast

class Config:
    _data: dict[str, Any]

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        return self._data[name]

def main() -> None:
    cfg = Config({"host": "localhost", "port": int32(8080)})
    host = cast(str, cfg.host)
    port = cast(int32, cfg.port)
    print(host)
    print(port)

main()

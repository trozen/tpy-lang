from tpy import Int32
from typing import Any, cast
class Config:
    _data: dict[str, Any]
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
    def __getattr__(self, name: str) -> Any:
        return self._data[name]
class Holder:
    cfg: Config
    def __init__(self, c: Config) -> None:
        self.cfg = c
    def host(self) -> str:
        return cast(str, self.cfg.host)
def a1(c: Config | None) -> str:
    if c is not None:
        return cast(str, c.host)
    return ""
def a2(c: Config) -> bool:
    key = "host"
    return hasattr(c, key.upper())
def a3(c: Config) -> bool:
    key = "host"
    return hasattr(c, key)
def main() -> None:
    cfg = Config({"host": "h"})
    print(Holder(cfg).host(), a1(cfg), a2(cfg), a3(cfg))
main()

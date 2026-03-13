# Test narrowing optional fields via truthiness (if obj.field:)
from typing import Optional

class Config:
    name: Optional[str]
    port: Optional[int]
    def __init__(self, name: Optional[str], port: Optional[int]) -> None:
        self.name = name
        self.port = port

def get_name(cfg: Config) -> str:
    if cfg.name:
        return cfg.name  # tpyc: ok
    return "default"

def get_port(cfg: Config) -> int:
    if cfg.port:
        return cfg.port  # tpyc: ok
    return 0

def test_negated(cfg: Config) -> str:
    if not cfg.name:
        return "missing"
    return cfg.name  # tpyc: ok

def main() -> None:
    c1 = Config("hello", 8080)
    c2 = Config(None, None)
    print(get_name(c1))
    print(get_name(c2))
    print(get_port(c1))
    print(get_port(c2))
    print(test_negated(c1))
    print(test_negated(c2))

main()

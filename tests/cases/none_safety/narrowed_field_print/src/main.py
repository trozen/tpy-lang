# Test print() on narrowed optional fields (value-type and reference-type)
from tpy import Int32
from typing import Optional

class Config:
    port: Optional[Int32]
    name: Optional[str]
    flag: Optional[bool]
    ratio: Optional[float]
    def __init__(self, port: Optional[Int32], name: Optional[str],
                 flag: Optional[bool], ratio: Optional[float]) -> None:
        self.port = port
        self.name = name
        self.flag = flag
        self.ratio = ratio

def show_guarded(cfg: Config) -> None:
    if cfg.port is not None:
        print(cfg.port)
    else:
        print("no port")
    if cfg.name is not None:
        print(cfg.name)
    else:
        print("no name")
    if cfg.flag is not None:
        print(cfg.flag)
    else:
        print("no flag")
    if cfg.ratio is not None:
        print(cfg.ratio)
    else:
        print("no ratio")

def show_truthy(cfg: Config) -> None:
    if cfg.port:
        print(cfg.port)
    if cfg.name:
        print(cfg.name)

def main() -> None:
    show_guarded(Config(8080, "test", True, 3.14))
    show_guarded(Config(None, None, None, None))
    show_truthy(Config(42, "hello", None, None))
    show_truthy(Config(None, None, None, None))

main()

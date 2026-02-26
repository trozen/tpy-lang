# Test narrowing optional fields with is None / is not None
from typing import Optional

class Config:
    port: Optional[int]
    def __init__(self, port: Optional[int]) -> None:
        self.port = port

def show_port(cfg: Config) -> None:
    if cfg.port is not None:
        p: int = cfg.port
        print(p)
    else:
        print("no port")

def main() -> None:
    show_port(Config(8080))
    show_port(Config(None))

main()

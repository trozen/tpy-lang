# Match on Literal type with overload dispatch and dead branch elimination --
# match narrows to single-value Literal, overload resolution picks the
# per-literal specialization, and branches are eliminated in the specialization
from typing import Literal, overload

@overload
def process(mode: Literal["r"]) -> str: ...
@overload
def process(mode: Literal["w"]) -> str: ...
@overload
def process(mode: Literal["rb"]) -> str: ...
def process(mode: str) -> str:
    if mode == "r":
        return "read"
    if mode == "w":
        return "write"
    return "binary"

def dispatch(mode: Literal["r", "w", "rb"]) -> None:
    match mode:
        case "r":
            print(process(mode))
        case "w":
            print(process(mode))
        case "rb":
            print(process(mode))

def main() -> None:
    dispatch("r")
    dispatch("w")
    dispatch("rb")

main()

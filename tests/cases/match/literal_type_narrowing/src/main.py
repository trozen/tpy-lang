# Match on Literal type with narrowing -- narrowed subject dispatches to correct overload
from typing import Literal, overload

@overload
def process(mode: Literal["r", "w"]) -> str: ...
@overload
def process(mode: Literal["rb", "wb"]) -> str: ...
def process(mode: str) -> str:
    if mode == "r" or mode == "w":
        return "text:" + mode
    return "binary:" + mode

def dispatch(mode: Literal["r", "w", "rb", "wb"]) -> None:
    match mode:
        case "r" | "w":
            print(process(mode))
        case "rb" | "wb":
            print(process(mode))

def main() -> None:
    dispatch("r")
    dispatch("rb")
    dispatch("w")
    dispatch("wb")

main()

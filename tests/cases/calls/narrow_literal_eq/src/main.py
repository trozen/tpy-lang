# Equality narrowing on Literal types: == / != narrows LiteralType params,
# enabling dispatch to more specific overload stubs after narrowing
from typing import Literal, overload
from tpy import Int32


@overload
def classify(mode: Literal["r", "w"]) -> str: ...

@overload
def classify(mode: Literal["rb", "wb"]) -> str: ...

def classify(mode: str) -> str:
    if mode == "r" or mode == "w":
        return "text"
    return "binary"


def dispatch_str(mode: Literal["r", "w", "rb", "wb"]) -> None:
    if mode == "rb":
        # mode: Literal["rb"], matches binary stub
        print(classify(mode))
    elif mode == "wb":
        # mode: Literal["wb"], matches binary stub
        print(classify(mode))
    elif mode == "r":
        # mode: Literal["r"], matches text stub
        print(classify(mode))
    else:
        # mode: Literal["w"], matches text stub
        print(classify(mode))


@overload
def bucket(x: Literal[1, 2]) -> str: ...

@overload
def bucket(x: Literal[3, 4]) -> str: ...

@overload
def bucket(x: Int32) -> str: ...

def bucket(x: Int32) -> str:
    if x <= 2:
        return "low"
    return "high"


def dispatch_int(x: Literal[1, 2, 3, 4]) -> None:
    if x == 1:
        # x: Literal[1], matches first stub
        print(bucket(x))
    elif x == 3:
        # x: Literal[3], matches second stub
        print(bucket(x))
    else:
        # x: Literal[2, 4] -- falls through to Int32 fallback
        print(bucket(x))


def dispatch_ne(mode: Literal["r", "rb"]) -> None:
    if mode != "r":
        # mode: Literal["rb"]
        print(classify(mode))
    else:
        # mode: Literal["r"]
        print(classify(mode))


def dispatch_or(mode: Literal["r", "w", "rb", "wb"]) -> None:
    if mode == "rb" or mode == "wb":
        # mode: Literal["rb", "wb"] (|| true intersects to nothing, but
        # false branch correctly narrows to Literal["r", "w"])
        print(classify(mode))
    else:
        # mode: Literal["r", "w"]
        print(classify(mode))


def dispatch_out_of_range(mode: Literal["r", "w"]) -> None:
    if mode == "rb":
        # "rb" not in {"r", "w"} -- no narrowing, no error
        print("unreachable")
    else:
        print("ok")


def main() -> None:
    dispatch_str("rb")
    dispatch_str("wb")
    dispatch_str("r")
    dispatch_str("w")

    dispatch_int(1)
    dispatch_int(3)
    dispatch_int(2)

    dispatch_ne("rb")
    dispatch_ne("r")

    dispatch_or("rb")
    dispatch_or("r")

    dispatch_out_of_range("r")


main()

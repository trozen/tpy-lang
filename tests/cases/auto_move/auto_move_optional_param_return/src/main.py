# Auto-move from value-optional params on return (std::move(*x) instead of (*x)).
from typing import Optional
from tpy import Own, String


class Payload:
    data: String
    count: int

    def __init__(self, data: String, count: int):
        self.data = data
        self.count = count


def unwrap_record(x: Optional[Own[Payload]]) -> Own[Payload]:
    if x is not None:
        return x
    return Payload("default", 0)


def unwrap_string(x: Optional[String]) -> String:
    if x is not None:
        return x
    return ""


def unwrap_bigint(x: Optional[int]) -> int:
    if x is not None:
        return x
    return 0


def unwrap_string_with_print(x: Optional[String]) -> String:
    if x is not None:
        print(x)  # non-last use -- should NOT move
        return x  # last use -- should move
    return ""


def main():
    print(unwrap_record(Payload("hello", 42)).data)
    print(unwrap_string("world"))
    print(unwrap_bigint(100))
    print(unwrap_string_with_print("test"))


main()

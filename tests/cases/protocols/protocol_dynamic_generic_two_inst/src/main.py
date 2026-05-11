# Same generic @dynamic protocol used with two distinct T's in one file.
# Container<int32_t> and Container<std::string> share the template but have
# independent vtables.
from typing import Protocol
from tpy import Int32, dynamic


@dynamic
class Container[T](Protocol):
    def get(self) -> T:
        ...


class IntBox:
    def get(self) -> Int32:
        return 5


class StrBox:
    def get(self) -> str:
        return "hi"


def show_int(c: Container[Int32]) -> None:
    print(c.get())


def show_str(c: Container[str]) -> None:
    print(c.get())


def main() -> None:
    show_int(IntBox())
    show_str(StrBox())


main()

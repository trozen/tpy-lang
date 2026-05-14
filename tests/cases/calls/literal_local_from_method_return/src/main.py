# Class method returning `Literal[str]` -- the method's C++ signature must emit
# `std::string_view` as the return type (same as free functions), and the
# receiving local picks up view storage end-to-end.
from typing import Literal


class Config:
    def get_mode(self) -> Literal["r", "w"]:
        return "r"


def main() -> None:
    c = Config()
    m: Literal["r", "w"] = c.get_mode()
    print(m)


main()

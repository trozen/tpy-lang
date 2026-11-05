# Class method returning `Literal[str]` -- the method returns as `-> str`
# does (an owned string, same as free functions), and the receiving local
# owns it, exactly as a `str` local bound to a `-> str` method call does.
from typing import Literal


class Config:
    def get_mode(self) -> Literal["r", "w"]:
        return "r"


def main() -> None:
    c = Config()
    m: Literal["r", "w"] = c.get_mode()
    print(m)


main()

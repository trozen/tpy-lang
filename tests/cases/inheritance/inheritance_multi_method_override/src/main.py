# Both bases provide greet(); the child overrides it, which resolves the ambiguity.
from tpy import Int32


class Speaker:
    def greet(self) -> str:
        return "hello"


class Greeter:
    def greet(self) -> str:
        return "hi"


class Both(Speaker, Greeter):  # tpyc: warning(/Both.greet.*hides/)
    def greet(self) -> str:
        return "greetings"


def main() -> None:
    b = Both()
    print(b.greet())


main()

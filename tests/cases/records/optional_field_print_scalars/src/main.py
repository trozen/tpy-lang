from tpy import int32
from dataclasses import dataclass


@dataclass
class Settings:
    count: int32 | None = None
    flag: bool | None = None
    ratio: float | None = None
    label: str | None = None


def main() -> None:
    s = Settings()
    print(s)

    s.count = 42
    s.flag = True
    s.ratio = 3.14
    s.label = "hello"
    print(s)

    s.flag = False
    s.ratio = 0.0
    print(s)

main()

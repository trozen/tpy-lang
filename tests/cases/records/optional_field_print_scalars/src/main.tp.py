from tpy import Int32, Bool


class Settings:
    count: Int32 | None
    flag: Bool | None
    ratio: float | None
    label: str | None

    def __init__(self) -> None:
        self.count = None
        self.flag = None
        self.ratio = None
        self.label = None


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

# Trivial field assigned inside control flow in __init__ produces a warning.
from tpy import Int32

class Config:
    value: Int32
    def __init__(self, flag: bool):
        if flag:
            self.value = 10  # tpyc: warning(/bypasses the C\+\+ member initializer list/)
        else:
            self.value = 20  # tpyc: warning(/bypasses the C\+\+ member initializer list/)

def main() -> None:
    c = Config(True)
    print(c.value)

main()

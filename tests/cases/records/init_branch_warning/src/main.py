# Field not initialized in init section produces a warning at the split point.
from tpy import Int32

class Config:
    value: Int32
    def __init__(self, flag: bool):
        if flag:  # tpyc: warning(/is not initialized before the constructor body/)
            self.value = 10  # tpyc: ok
        else:
            self.value = 20  # tpyc: ok

def main() -> None:
    c = Config(True)
    print(c.value)

main()

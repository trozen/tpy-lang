# StrView as a record field (caller must ensure data outlives the object).
from tpy import StrView

class Wrapper:
    sv: StrView

    def __init__(self, sv: str) -> None:
        self.sv = sv

    def get(self) -> StrView:
        return self.sv

def main() -> None:
    w = Wrapper("hello")
    print(w.get())

main()

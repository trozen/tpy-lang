from tpy import Int32
class H:
    opt: Int32 | None
    def __init__(self) -> None:
        self.opt = 1
def main() -> None:
    hs = [H(), H()]
    if hs[0].opt:
        print("x")
main()

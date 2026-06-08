# class Foo(Sync) opt-in: verified claim, parallel to the Send opt-in.
from tpy import Int32, Send, Sync

class Config(Send, Sync):
    rate: Int32
    depth: Int32

    def __init__(self, rate: Int32, depth: Int32) -> None:
        self.rate = rate
        self.depth = depth

def main() -> None:
    c = Config(1, 2)  # tpyc: is_send(yes) is_sync(yes)
    print(c.rate, c.depth)

main()

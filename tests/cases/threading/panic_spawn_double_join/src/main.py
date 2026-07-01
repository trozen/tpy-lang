# Consuming a JoinHandle twice is a runtime panic (the handle is spent after the
# first join()).
from tpy.thread import spawn


class One:
    def run(self) -> int:
        return 1


def main() -> None:
    h = spawn[int, One](One())
    print(h.join())
    print(h.join())   # panic: handle already consumed


main()

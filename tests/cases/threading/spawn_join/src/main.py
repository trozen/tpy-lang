# spawn moves a Send task onto a fresh OS thread; join() blocks and returns the
# task's result. Two tasks run concurrently and are joined. Each owns a list
# moved in from the caller and mutated on the worker thread; the @nocopy task
# type makes a silent copy a compile error, proving the task was moved (not
# copied) across the spawn boundary.
from tpy import Own, nocopy
from tpy.thread import spawn


@nocopy
class Summer:
    data: list[int]

    def __init__(self, data: Own[list[int]]) -> None:
        self.data = data

    def run(self) -> int:
        self.data.append(100)
        total = 0
        for v in self.data:
            total += v
        return total


def main() -> None:
    a = spawn[int, Summer](Summer([1, 2, 3]))
    b = spawn[int, Summer](Summer([10, 20]))
    print("a:", a.join())   # 1+2+3+100
    print("b:", b.join())   # 10+20+100


main()

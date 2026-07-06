# spawn(task) with NO explicit type args: T is inferred through the
# Send[Own[T]] wrapper and R via associated-type inference from the task's
# run() return type. The @nocopy task owns a list moved in and mutated on the
# worker (a silent copy across the spawn boundary would be a compile error).
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
    a = spawn(Summer([1, 2, 3]))    # tpyc: ok
    b = spawn(Summer([10, 20]))
    print("a:", a.join())   # 1+2+3+100
    print("b:", b.join())   # 10+20+100


main()

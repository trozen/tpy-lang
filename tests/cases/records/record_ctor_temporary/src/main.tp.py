from tpy import Int32

class Numbers:
    data: list[Int32]

    def __init__(self, items: list[Int32]) -> None:
        self.data = items

    def sum(self) -> Int32:
        total: Int32 = 0
        i: Int32 = 0
        while i < len(self.data):
            total += self.data[i]
            i += 1
        return total

def main() -> None:
    # Constructor with temporary list literal
    n1: Numbers = Numbers([1, 2, 3, 4, 5])
    print(n1.sum())  # 15

    # Constructor with variable
    items: list[Int32] = [10, 20, 30]
    n2: Numbers = Numbers(items)
    print(n2.sum())  # 60

main()

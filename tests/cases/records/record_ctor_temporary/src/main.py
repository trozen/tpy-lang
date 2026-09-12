from tpy import int32

class Numbers:
    data: list[int32]

    def __init__(self, items: list[int32]) -> None:
        self.data = items

    def sum(self) -> int32:
        total: int32 = 0
        i: int32 = 0
        while i < len(self.data):
            total += self.data[i]
            i += 1
        return total

def main() -> None:
    # Constructor with temporary list literal
    n1: Numbers = Numbers([1, 2, 3, 4, 5])
    print(n1.sum())  # 15

    # Constructor with variable
    items: list[int32] = [10, 20, 30]
    n2: Numbers = Numbers(items)
    print(n2.sum())  # 60

main()

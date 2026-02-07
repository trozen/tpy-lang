from tpy import StaticList, Int32, Span

class MyList(StaticList[Int32, 10]):
    label: str

    def __init__(self, label: str, items: Span[Int32]) -> None:
        super().__init__(items)
        self.label = label

nums: list[Int32] = [1, 2, 3]
m = MyList("test", nums)
print(m[0])
print(m[1])
print(m[2])
print(m.label)

from tpy import Int32
class Dog:
    def sound(self) -> str:
        return "woof"
class Cat:
    def sound(self) -> str:
        return "meow"
async def step(n: Int32) -> Int32:
    return n + 1
async def f(d: dict[Int32, Int32]) -> Int32:
    if sum(k for k in d) > 1:
        await step(1)
        return 1
    return 0
def main() -> None:
    pass
main()

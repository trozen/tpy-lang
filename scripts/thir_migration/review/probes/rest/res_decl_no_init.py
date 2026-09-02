from tpy import Int32
class Dog:
    def sound(self) -> str:
        return "woof"
class Cat:
    def sound(self) -> str:
        return "meow"
async def step(n: Int32) -> Int32:
    return n + 1
async def f() -> Int32:
    x: Int32
    await step(0)
    x = 1
    return x
def main() -> None:
    pass
main()

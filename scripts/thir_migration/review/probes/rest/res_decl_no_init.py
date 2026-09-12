from tpy import int32
class Dog:
    def sound(self) -> str:
        return "woof"
class Cat:
    def sound(self) -> str:
        return "meow"
async def step(n: int32) -> int32:
    return n + 1
async def f() -> int32:
    x: int32
    await step(0)
    x = 1
    return x
def main() -> None:
    pass
main()

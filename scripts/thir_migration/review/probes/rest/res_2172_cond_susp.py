from tpy import int32
class Dog:
    def sound(self) -> str:
        return "woof"
class Cat:
    def sound(self) -> str:
        return "meow"
async def step(n: int32) -> int32:
    return n + 1
async def f(d: dict[int32, int32]) -> int32:
    if sum(k for k in d) > 1:
        await step(1)
        return 1
    return 0
def main() -> None:
    pass
main()

from tpy import Int32
class Dog:
    def sound(self) -> str:
        return "woof"
class Cat:
    def sound(self) -> str:
        return "meow"
async def step(n: Int32) -> Int32:
    return n + 1
async def describe(a: Dog | Cat) -> str:
    if isinstance(a, Dog):
        return "dog"
    await step(0)
    return a.sound()
def main() -> None:
    pass
main()

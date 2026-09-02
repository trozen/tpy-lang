from typing import overload
class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives
@overload
def judge(a: Dog, n: int) -> str: ...
@overload
def judge(a: Cat, n: int) -> str: ...
def judge(a: Dog | Cat, n: int) -> str:
    if n > 3:
        return 'big'
    elif isinstance(a, Dog):
        return a.name
    else:
        return str(a.lives)
def main() -> None:
    print(judge(Dog('d'), 1))
main()

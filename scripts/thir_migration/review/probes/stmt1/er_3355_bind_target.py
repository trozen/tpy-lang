from tpy import int32, Own, error_return, ReturnException
class Err(Exception, ReturnException):
    pass
class Cat:
    n: int32
    def __init__(self) -> None:
        self.n = 1
class Dog:
    n: int32
    def __init__(self) -> None:
        self.n = 2
class Maker:
    @staticmethod
    @error_return(Err)
    def make(n: int32) -> Own[Cat]:
        if n < 0:
            raise Err
        return Cat()
@error_return(Err)
def caller(n: int32) -> int32:
    p: Cat | Dog = Dog()
    p = Maker.make(n)
    if isinstance(p, Cat):
        return p.n
    return 0
def main() -> None:
    try:
        print(caller(2))
    except Err:
        print("err")
main()

from tpy import int32, error_return, ReturnException
class Bad(Exception, ReturnException):
    pass
class P:
    @staticmethod
    @error_return(Bad)
    def parse(s: str) -> int32:
        if s == "":
            raise Bad
        return 1
@error_return(Bad)
def go(s: str) -> int32:
    v = P.parse(s) + 1
    return v
def main() -> None:
    try:
        print(go("x"))
    except Bad:
        print("bad")
main()

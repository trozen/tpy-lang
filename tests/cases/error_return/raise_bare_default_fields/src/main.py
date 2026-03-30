# raise E (bare) on a type with fields but no __init__ default-constructs
from tpy import Int32, error_return, ReturnException

class MyError(Exception, ReturnException):
    code: Int32

@error_return(MyError)
def fail() -> Int32:
    raise MyError

def main() -> None:
    try:
        v = fail()
    except MyError as e:
        print(e.code)

main()

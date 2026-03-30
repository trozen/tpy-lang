# except ReturnException as e is not supported
from tpy import Int32, error_return, ReturnException

class MyError(Exception, ReturnException):
    pass

@error_return(MyError)
def foo() -> Int32:
    raise MyError

def main() -> None:
    try:  # tpyc: error(/except ReturnException as.*not supported/)
        v = foo()
    except ReturnException as e:
        print("error")

main()

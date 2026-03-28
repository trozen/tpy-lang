# except ControlFlow as e is not supported
from tpy import Int32, error_return, ControlFlow

class MyError(Exception, ControlFlow):
    pass

@error_return(MyError)
def foo() -> Int32:
    raise MyError

def main() -> None:
    try:  # tpyc: error(/except ControlFlow as.*not supported/)
        v = foo()
    except ControlFlow as e:
        print("error")

main()

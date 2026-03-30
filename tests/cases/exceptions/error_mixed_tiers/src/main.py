# Error: mixing ReturnException and non-ReturnException in same try/except
from tpy import Int32, error_return, ReturnException

class NotFound(Exception, ReturnException):
    pass

@error_return(NotFound)
def lookup() -> Int32:
    raise NotFound

def main() -> None:
    try:  # tpyc: error(/cannot mix ReturnException and non-ReturnException/)
        v = lookup()
    except NotFound:
        print("not found")
    except ValueError:
        print("value error")

main()

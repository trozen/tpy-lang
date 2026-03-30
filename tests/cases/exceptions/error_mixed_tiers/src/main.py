# Error: mixing ControlFlow and non-ControlFlow in same try/except
from tpy import Int32, error_return, ControlFlow

class NotFound(Exception, ControlFlow):
    pass

@error_return(NotFound)
def lookup() -> Int32:
    raise NotFound

def main() -> None:
    try:  # tpyc: error(/cannot mix ControlFlow and non-ControlFlow/)
        v = lookup()
    except NotFound:
        print("not found")
    except ValueError:
        print("value error")

main()

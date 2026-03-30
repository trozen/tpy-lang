# Error: except handler with a type that doesn't inherit from Exception
class NotAnException:
    pass

def main() -> None:
    try:  # tpyc: error(/not an exception type/)
        print("try")
    except NotAnException:
        print("caught")

main()

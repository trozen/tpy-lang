# Error: raise a non-exception type
class NotAnException:
    pass

def main() -> None:
    raise NotAnException  # tpyc: error(/not an exception type/)

main()

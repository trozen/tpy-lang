# Unhandled exception terminates with message
def fail() -> None:
    raise ValueError("something went wrong")

def main() -> None:
    fail()

main()

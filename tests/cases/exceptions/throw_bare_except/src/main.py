# Bare except: catches any exception
def fail() -> None:
    raise ValueError("oops")

def main() -> None:
    try:
        fail()
    except:
        print("caught something")

main()

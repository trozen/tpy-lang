# match/case on str subject with literal patterns
def handle(cmd: str) -> str:
    match cmd:
        case "quit":
            return "quitting"
        case "help":
            return "showing help"
        case other:
            return "unknown: " + other

def main() -> None:
    print(handle("quit"))
    print(handle("help"))
    print(handle("foo"))

main()

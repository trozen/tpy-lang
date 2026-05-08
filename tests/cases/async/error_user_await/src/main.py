# user-defined __await__ method is not yet supported in v1; v3+ may
# add user CPython __await__ adaptation.
class CustomAwaitable:
    def __await__(self) -> int:  # tpyc: error(/user-defined '__await__' is not yet supported/)
        return 42

def main() -> None:
    print("ok")

main()

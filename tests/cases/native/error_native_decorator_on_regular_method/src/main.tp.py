class A:  # tpyc: error(/@native.*only allowed on @native/)
    @native("x")
    def f(self) -> int:
        return 1

def main() -> None:
    pass

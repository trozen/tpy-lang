class A:  # tpyc: error(/cannot have '...' body on a regular class/)
    def f(self) -> int:
        ...

def main() -> None:
    pass

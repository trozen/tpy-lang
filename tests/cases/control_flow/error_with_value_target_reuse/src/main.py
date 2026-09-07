# The same `with ... as` target name reused for a VALUE (non-record) enter
# result: the already-declared arm has no render for that slot.
# The second `with Seven() as x:` rejects.
class Seven:
    def __enter__(self) -> int:
        return 7

    def __exit__(self, t: None, v: None, tb: None) -> None:
        pass


def run() -> None:
    with Seven() as x:
        print(x)
    with Seven() as x:  # tpyc: error(/stmt.with/)
        print(x)


def main() -> None:
    run()


main()

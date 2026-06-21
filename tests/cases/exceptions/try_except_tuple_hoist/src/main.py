# str tuple-unpack targets assigned on all paths of a try/except, read after:
# hoisted out of the try, they must own (std::string), not dangle into the temp.
def maybe_pair(fail: bool) -> tuple[str, str]:
    if fail:
        raise OSError("x")
    return ("hello-world-long", "another-long-str")


def run(fail: bool) -> str:
    try:
        a, b = maybe_pair(fail)
    except OSError:
        a, b = ("fb-a-long-enough", "fb-b-long-enough")
    return a + "|" + b


def main() -> None:
    print(run(False))
    print(run(True))


main()

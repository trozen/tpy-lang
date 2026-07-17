# Error: every element of an `except (...)` tuple must be a simple or dotted
# name, same as the plain form's requirement.
class AErr(Exception):
    pass


def make() -> AErr:
    return AErr()


def main() -> None:
    try:
        pass
    except (AErr, make().Error):  # tpyc: error(/requires a simple or dotted name/)
        pass


main()

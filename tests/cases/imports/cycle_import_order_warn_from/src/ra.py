# The cycle import comes first on the line, so `ra` has not bound B yet
# when `rb` runs.
import rb; from rt import B  # noqa: E702


def main() -> None:
    print("same line:", rb.make().k)

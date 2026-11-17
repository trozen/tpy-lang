# `wa` lists E in `__all__`, then starts `wb` before it defines E.
__all__ = ["E", "main"]
import wb


class E(Exception):
    pass


def main() -> None:
    print("from:", wb.catch())

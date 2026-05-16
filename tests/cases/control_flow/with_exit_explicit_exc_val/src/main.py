# Mirror of with_exit_normal_path but with `exc_val: Optional[BaseException]`
# spelled out explicitly. Pins that the user-written annotation produces the
# same compile + runtime behavior as the synthesized default (unannotated
# params). The existing none_annotation_positions test covers the
# `exc_val: None` opt-out form; this test covers the inspect form.

from typing import Optional


class Tracker:
    def __enter__(self) -> int:
        print("enter")
        return 7

    def __exit__(
        self,
        exc_type: None,
        exc_val: Optional[BaseException],
        exc_tb: None,
    ) -> None:
        if exc_val is None:
            print("clean exit")
        else:
            print(f"exception exit: {str(exc_val)}")


def main() -> None:
    with Tracker() as a:
        print(f"body a={a}")
    print("---")
    try:
        with Tracker() as b:
            print(f"body b={b}")
            raise ValueError("explicit")
    except ValueError as e:
        print(f"outer caught: {str(e)}")


main()

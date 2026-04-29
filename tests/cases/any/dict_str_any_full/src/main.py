# The canonical dynamic-config use case end-to-end: heterogeneous
# dict[str, Any], auto-coerce on assignment, isinstance narrowing on
# lookup, explicit typing.cast, and universal ops on raw Any. Exercises
# every Any feature in one program -- the integration test.

from typing import Any, cast


def main() -> None:
    cfg: dict[str, Any] = {
        "host": "localhost",
        "port": 8080,
        "debug": True,
        "timeout": 1.5,
    }

    # isinstance narrowing: borrow into the Any cell, outer survives.
    debug = cfg["debug"]
    if isinstance(debug, bool):
        if debug:
            print("debug on")
        else:
            print("debug off")

    # Auto-coerce: annotated assignment runs any_cast_or_panic.
    host: str = cfg["host"]
    port: int = cfg["port"]
    print(host)
    print(port)

    # Explicit typing.cast: same runtime check, user-driven.
    timeout = cast(float, cfg["timeout"])
    print(timeout)

    # Print on raw Any -- universal op via the str slot.
    print(cfg["host"])


main()

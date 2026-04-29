# dict[str, Any]: the canonical dynamic-config use case. Phase 3 covers
# construction only; lookups, narrowing, and auto-coerce land later.

from typing import Any


def main() -> None:
    cfg: dict[str, Any] = {
        "name": "tpy",
        "version": 1,
        "debug": True,
    }
    print(cfg)
    print(len(cfg))


main()

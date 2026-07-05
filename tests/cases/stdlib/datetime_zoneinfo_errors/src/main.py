# datetime v4 zoneinfo error surface (all catchable at runtime, so match
# by type token): unknown key -> ZoneInfoNotFoundError (a KeyError
# subclass), malformed keys -> ValueError per CPython's tzpath rules.
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def probe(key: str) -> str:
    try:
        z = ZoneInfo(key)
        return "ok(" + z.key + ")"
    except ZoneInfoNotFoundError:
        return "ZoneInfoNotFoundError"
    except ValueError:
        return "ValueError"


def main() -> None:
    print(probe("Europe/Warsaw"))
    print(probe("Not/AZone"))
    print(probe(""))
    print(probe("../etc/passwd"))
    print(probe("/etc/localtime"))
    print(probe("Europe//Warsaw"))
    # The subclass is catchable as its KeyError base.
    try:
        z = ZoneInfo("Also/NotAZone")
        print("no-raise", z.key)
    except KeyError:
        print("KeyError-base")


main()

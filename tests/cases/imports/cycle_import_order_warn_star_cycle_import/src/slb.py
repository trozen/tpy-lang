from sla import *  # tpyc: warning(/'sla' is still being imported -- .* before it binds 'date' \(sla\.py:3\); .*\. Bind 'date' above that import in sla\.py, or import 'slb' before 'sla' in main\.py\.$/)


def main() -> None:
    print("same line:", d_year(date(2026, 11, 17)))


def d_year(d: date) -> int:
    return d.year

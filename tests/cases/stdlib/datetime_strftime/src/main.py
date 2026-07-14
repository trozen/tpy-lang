# datetime v3 strftime: full directive set on date/time/datetime with the
# CPython-on-glibc edge behaviors -- %c/%x/%X C-locale compositions, ISO
# %G/%V/%u across year rollovers, %U/%W week-number edges, %I/%p
# midnight/noon, %z/%Z empty when naive and seconds-bearing when the offset
# has them, unknown directives passed through verbatim, trailing lone % kept.
# %Y/%G on tiny years (1, 42) are NOT pinned here: CPython 3.14 zero-pads
# them to 4 digits ("1" -> "0001") while TPy/older CPython do not, so the
# padding is version-specific; %Y/%G are covered on normal years above.
from datetime import datetime, date, time, timedelta, timezone


def main() -> None:
    d = date(2021, 3, 5)
    print(d.strftime("%a %A %b %B %d %m %y %Y %j %w %u"))
    print(d.strftime("%c"))
    print(d.strftime("%x | %X"))
    print(d.strftime("%U %W %G %V"))
    print(date(1, 1, 1).strftime("%y %U %W %V %u %j %c"))
    print(date(2016, 1, 1).strftime("%U %W %G %V %u"))
    print(date(2018, 12, 31).strftime("%U %W %G %V %u"))
    print(date(2019, 1, 1).strftime("%U %W %G %V %u"))
    print(date(2020, 12, 31).strftime("%G-%V-%u"))
    print(d.strftime("%z|%Z|"))
    print(d.strftime("100%% %q %"))

    t = time(0, 5, 3, 40)
    print(t.strftime("%H %I %p %M %S %f %j %Y %a"))  # 1900-01-01 timetuple
    print(time(12, 0).strftime("%I %p"))
    print(time(13, 30).strftime("%I %p"))

    ist = timezone(timedelta(hours=5, minutes=30), "IST")
    dt = datetime(2021, 3, 5, 14, 30, 15, 123456, tzinfo=ist)
    print(dt.strftime("%Y-%m-%d %H:%M:%S.%f %z %Z"))
    print(dt.strftime("%c"))
    print(datetime(2021, 3, 5, 1, 2, 3,
                   tzinfo=timezone(timedelta(hours=-3, minutes=-30)))
          .strftime("%z %Z"))
    print(datetime(2021, 3, 5,
                   tzinfo=timezone(timedelta(hours=5, minutes=30,
                                             seconds=15)))
          .strftime("%z %Z"))
    print(datetime(2021, 3, 5, 14, 30).strftime("|%z %Z|"))


main()

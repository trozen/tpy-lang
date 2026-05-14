{ Subrange type alias and set-membership `in` with literal-element
  sets over integers and over enum members. M12 makes subrange
  assignments bounds-checked at translate time via the runtime
  helper `check_subrange`. }
program SubrangeIn;
type
  Byte = 0..255;
  Day = (Mon, Tue, Wed, Thu, Fri, Sat, Sun);
var
  b: Byte;
  d: Day;
begin
  b := 42;
  writeln(b);
  d := Sat;
  if d in [Sat, Sun] then writeln('weekend')
  else writeln('weekday');
  if 5 in [1, 3, 5, 7, 9] then writeln('odd small');
end.

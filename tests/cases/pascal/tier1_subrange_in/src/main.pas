{ Tier-1: subrange type (parsed as alias to integer; no bounds
  enforcement in M10) and set-membership `in` with literal-element
  sets over integers and over enum members. }
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

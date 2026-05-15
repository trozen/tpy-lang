{ M7: enum equality comparison + boolean composition (and / <>). }
program DayCmp;
type
  Day = (Mon, Tue, Wed, Thu, Fri, Sat, Sun);
var
  d: Day;
begin
  d := Fri;
  if d = Fri then
    writeln('TGIF')
  else
    writeln('not yet');
  if (d <> Sat) and (d <> Sun) then
    writeln('weekday')
  else
    writeln('weekend');
end.

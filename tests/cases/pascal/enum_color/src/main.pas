{ M7: `type Color = (Red, Green, Blue);` -- enum declaration,
  unqualified member assignment, case/of with enum value arms. }
program ColorCase;
type
  Color = (Red, Green, Blue);
var
  c: Color;
begin
  c := Green;
  case c of
    Red: writeln('red');
    Green: writeln('green');
    Blue: writeln('blue');
  end;
end.

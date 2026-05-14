{ M17: writeln / write with multiple args. Each argument prints
  via TPy's `print` builtin with `end=''` so they stream into one
  line; only the trailing arg of a `writeln` gets the newline. }
program WritelnMultiarg;
var
  x, y: integer;
begin
  x := 7;
  y := 42;
  writeln('x=', x, ' y=', y);
  write('a=', x);
  write(' b=', y);
  writeln;
  writeln('done');
end.

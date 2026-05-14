{ Tier-1: `const` block with integer, real, string, and boolean
  initialisers, used in expressions and writeln. }
program ConstTest;
const
  N = 10;
  Pi = 3.14159;
  Greeting = 'Hello';
  Enabled = true;
var
  i: integer;
begin
  i := N + 5;
  writeln(i);
  writeln(Pi);
  writeln(Greeting);
  if Enabled then writeln('on') else writeln('off');
end.

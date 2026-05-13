{ M6: string concatenation, length(), equality comparison.
  Exercises chained `+` across PStr variables and string literals. }
program StringOps;
var
  greeting: string;
  name: string;
  result: string;
begin
  greeting := 'Hello';
  name := 'World';
  result := greeting + ', ' + name + '!';
  writeln(result);
  writeln(length(result));
  if result = 'Hello, World!' then
    writeln('match')
  else
    writeln('no match');
end.

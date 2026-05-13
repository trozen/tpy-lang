{ M8: stdin-driven number guesser. Reads integer guesses via readln
  and compares against a fixed target. The driving stdin sequence
  lives in src/input.txt: 3 (too low), 10 (too high), 7 (correct). }
program Guess;
var
  target, attempt: integer;
begin
  target := 7;
  attempt := 0;
  while attempt <> target do
  begin
    writeln('Enter a guess:');
    readln(attempt);
    if attempt < target then
      writeln('too low')
    else if attempt > target then
      writeln('too high');
  end;
  writeln('Correct!');
end.

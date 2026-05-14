{ M13: text-file I/O end-to-end. Writes a header line plus four
  integers to `out.txt`, closes, reopens for read, and reads each
  line back into the program. Exercises `Assign` / `Rewrite` /
  `Reset` / `Close` / `Writeln(f, ...)` / `Readln(f, ...)` and
  `Eof(f)` in the loop guard. }
program TextFileIO;
var
  f: text;
  line: string;
  n, sum, i: integer;
begin
  assign(f, 'out.txt');
  rewrite(f);
  writeln(f, 'numbers');
  for i := 1 to 4 do
    writeln(f, i * 10);
  close(f);

  assign(f, 'out.txt');
  reset(f);
  readln(f, line);
  write('header: ');
  writeln(line);
  sum := 0;
  while not eof(f) do
  begin
    readln(f, n);
    sum := sum + n;
  end;
  close(f);
  write('sum: ');
  writeln(sum);
end.

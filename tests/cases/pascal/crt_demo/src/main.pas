{ M14: Crt portable subset. Exercises foreground/background color,
  screen and cursor control, the no-op timing calls, and `ReadKey`
  (driven by an input.txt stdin fixture so the test runs head-
  lessly). Expected stdout contains the literal ANSI escape codes
  the Pascal-side runtime emits. }
program CrtDemo;
uses Crt;
var
  ch: char;
begin
  ClrScr;
  TextColor(Red);
  TextBackground(Yellow);
  GotoXY(3, 5);
  write('hello');
  TextColor(LightGreen);
  TextBackground(Black);
  ClrEol;
  writeln(' world');
  Delay(0);
  Sound(440);
  NoSound;
  write('press a key: ');
  ch := ReadKey;
  writeln(ch);
end.

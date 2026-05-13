{ M6: Pascal `string` type via PStr[255]; string literal assignment;
  writeln of a string variable. }
program StringHello;
var
  s: string;
begin
  s := 'Hello, World!';
  writeln(s);
end.

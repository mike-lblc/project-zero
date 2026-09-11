' Запуск без окна. Планировщик Windows иначе показывает чёрное окно консоли
' при каждом срабатывании — раз в пять минут, что владелец справедливо назвал
' помехой. WScript.Shell.Run с кодом 0 запускает то же самое скрыто.
' Аргумент: полный путь к скрипту python.
Set sh = CreateObject("WScript.Shell")
script = WScript.Arguments(0)
sh.Run "py -3.13 -X utf8 """ & script & """", 0, False

"""РЕШАТЕЛЬ ПРОВЕРОЧНОЙ ЗАДАЧИ MOLTBOOK.

Владелец разрешил прямо: агенты решают проверочную задачу, чтобы публиковать.
Это не капча против роботов и не выдача себя за человека — площадка САМА просит
ИИ-агентов решить обфусцированную арифметическую задачку, чтобы доказать, что
они понимают язык. Задача всегда одна и та же по форме: два числа и одно
действие (+, -, *, /), зашумлённые символами, разбитыми словами и чередующимся
регистром (тема — лобстеры и физика).

ОСТОРОЖНОСТЬ ОБЯЗАТЕЛЬНА. Десять проваленных попыток подряд — автоматическая
блокировка аккаунта. Поэтому решатель отвечает ТОЛЬКО когда уверен: ровно два
числа и одно понятное действие извлеклись. Если разбор неоднозначен — он молчит
и возвращает None, а не гадает и не жжёт попытки.

УРОК СТРЕСС-ТЕСТА (4000 задач, 14.09.2026): 2,9% уверенных НЕВЕРНЫХ ответов —
глаголы искались точно, а числа нечётко: обфусцированный глагол терялся, и
единственным «действием» оставалось «per» из «meters per second». И короткие
служебные слова («to», «for», «on») на одну правку от «two», «four», «one»
рождали фантомные числа. Правила: глаголы нормализуются как числа; предлоги-
единицы («per», «over») действием не считаются; одна правка — только у слов
не короче пяти букв.

УРОК ЖИВОЙ ЗАДАЧИ (14.09.2026): настоящая обфускация РВЁТ СЛОВА ПРОБЕЛАМИ и
удваивает буквы в смешанном регистре — «tW]eN tY» = twenty, «sEeV eN» = seven,
«iInCrEeA sEs» = increases. Поэтому токены-осколки СКЛЕИВАЮТСЯ: на каждой
позиции пробуются 1..4 соседних осколка, и склейка принимается, только если
после схлопывания повторов она ТОЧНО равна числительному или слову действия.
Подстроки внутри слов не ищутся («one» внутри «another» — фантом), правка на
одну букву у склеек не допускается. Три числа или два действия — молчание.
"""
import re

# Числа словами. Хватает диапазона, который встречается в задачках.
_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
          "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
          "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALE = {"hundred": 100, "thousand": 1000}
_ALL_WORDS = {**_UNITS, **_TENS, **_SCALE}

# Фразы действия. Единицы измерения и предлоги («per», «over») намеренно
# исключены — см. урок стресс-теста в шапке.
_ADD = ("speed up", "speeds up", "sped up", "gains", "gain", "gained", "increase",
        "increases", "increased", "plus", "more", "faster by", "up by", "added", "add",
        "adds", "rises", "rise", "raised by", "grows by", "grow by", "climbs by",
        "accelerates by", "boosts by", "boosted by", "goes up", "sum of", "total of",
        "combined with", "together with")
_SUB = ("slow", "slows", "slowed", "loses", "lose", "lost", "decrease", "decreases",
        "decreased", "minus", "less", "fewer", "down by", "drops", "drop", "reduced",
        "reduce", "reduces", "falls", "fall", "slower by", "subtract", "subtracts",
        "subtracted", "take away", "takes away", "removes", "removed", "shrinks by",
        "declines by", "decelerates by", "cut by", "cuts", "difference between")
_MUL = ("times", "multiplied", "multiply", "multiplies", "product of", "twice",
        "double", "doubles", "doubled", "triple", "triples", "tripled", "quadruple")
_DIV = ("divided", "divide", "divides", "split", "splits", "shared", "shares",
        "half of", "halved", "quarter of", "ratio of")

_OP_PHRASES = (("*", _MUL), ("/", _DIV), ("+", _ADD), ("-", _SUB))
# Словарь отдельных слов всех фраз — к нему нормализуются токены текста.
_OP_VOCAB = sorted({w for _, phrases in _OP_PHRASES for p in phrases for w in p.split()})
_VOCAB = sorted(set(_ALL_WORDS) | set(_OP_VOCAB))
_COLLAPSED = {}          # схлопнутое слово словаря -> слово; заполняется ниже

FUZZY_MIN_LEN = 5          # одна правка допустима только у слов не короче пяти букв
MAX_JOIN = 10              # осколков подряд: «eighteen» рвался на семь, «multiplied» — 10 букв

# Слова-наполнители из лексики задач. К ответу отношения не имеют — они нужны,
# чтобы разбиение на слова предпочитало «on | eight», а не фантомное «one | ight»:
# распознанное слово стоит 0, нераспознанный осколок — 1, и выигрывает разбор
# с наименьшей суммой.
_FILLER = frozenset("""
a an the and at to in on of by with per for from into over under up down off out
lobster lobsters crab crabs shrimp reef reefs tide pool ocean sea water wave waves
swims swim swimming moves move moving travels travel goes go went starts start starting
speed velocity meters meter centimeters centimeter cm seconds second minute minutes hour
hours claws claw holes hole shell shells legs leg antenna another other rival rivals
combined total final new whats what is it its each how many much result answer find
then now later after before so that this these those there here where when which who
are was were be been has have had does do did will would can could should faster slower
more less than same value way trip evenly among between together sum difference
""".split())


def _deobfuscate(text):
    """Снимает шум: только буквы, цифры и пробелы, нижний регистр."""
    t = re.sub(r"[^0-9a-zA-Z\s]", "", str(text or ""))
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def _collapse(w):
    """Схлопывает подряд идущие одинаковые буквы: twenntyy -> twenty."""
    return re.sub(r"(.)\1+", r"\1", w)


for _w in _VOCAB:
    _COLLAPSED.setdefault(_collapse(_w), _w)


def _edit_le1(a, b):
    """True, если строки различаются не более чем на одну правку (вставка/удаление/замена)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) == 1
    long, short = (a, b) if la > lb else (b, a)
    for i in range(len(long)):
        if long[:i] + long[i + 1:] == short:
            return True
    return False


def _exact_vocab(cand):
    """Слово словаря, равное кандидату точно или после схлопывания повторов, или None."""
    if cand in _COLLAPSED and _COLLAPSED[cand] == cand:
        return cand
    c = _collapse(cand)
    return _COLLAPSED.get(c)


def _fuzzy_vocab(tok):
    """Как _exact_vocab, но для ОДИНОЧНОГО длинного токена допускает одну правку.

    Короткие слова только точно: иначе «to»/«for»/«on» превращаются в
    «two»/«four»/«one».
    """
    w = _exact_vocab(tok)
    if w is not None:
        return w
    c = _collapse(tok)
    if len(c) >= FUZZY_MIN_LEN:
        for cw, w in _COLLAPSED.items():
            if len(cw) >= FUZZY_MIN_LEN and _edit_le1(c, cw):
                return w
    return None


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _known(cand):
    """Слово словаря чисел/действий или наполнитель, равное кандидату точно
    либо после схлопывания повторов; иначе None."""
    w = _exact_vocab(cand)
    if w is not None:
        return w
    c = _collapse(cand)
    return c if c in _FILLER else None


def _segment(tokens):
    """Склеивает осколки слов обратно в слова, выбирая разбор с наименьшим числом
    нераспознанных осколков (динамическое программирование справа налево).

    Кандидаты на позиции: одиночный осколок (число; слово словаря — длинное и
    с одной правкой; наполнитель; иначе сырой осколок ценой 1) и склейки из
    2..MAX_JOIN осколков — только при ТОЧНОМ совпадении после схлопывания.
    При равной цене — разбор с меньшим числом слов.

    Почему цена, а не жадность: жадная склейка «times»+«s» съедала «s» из
    «sixty» и теряла число (16 неверных из 4000). Разбор «times | sixty»
    распознаёт оба слова и стоит 0, «times+s | ixty» стоит 1 — цена решает.
    Правило «не склеивать повтор последней буквы» пробовалось и отвергнуто:
    оно ломало слова с настоящими сдвоенными буквами (sixt|e|e|n, thr|e|e).
    """
    n = len(tokens)
    best = [None] * (n + 1)          # best[i] = (цена, слов, слова) для tokens[i:]
    best[n] = (0, 0, [])
    for i in range(n - 1, -1, -1):
        tok = tokens[i]
        options = []
        if _NUM_RE.fullmatch(tok):
            options.append((0, tok, 1))
        else:
            w = _fuzzy_vocab(tok)
            if w is None and _collapse(tok) in _FILLER:
                w = _collapse(tok)
            options.append((0, w, 1) if w is not None else (1, tok, 1))
            for k in range(2, min(MAX_JOIN, n - i) + 1):
                parts = tokens[i:i + k]
                if _NUM_RE.fullmatch(parts[-1]):
                    break                    # цифры не склеиваем со словами
                w = _known("".join(parts))
                if w is not None:
                    options.append((0, w, k))
        choice = None
        for cost, w, k in options:
            rest = best[i + k]
            cand = (cost + rest[0], 1 + rest[1], [w] + rest[2])
            if choice is None or cand[:2] < choice[:2]:
                choice = cand
        best[i] = choice
    return best[0][2]


def _numbers(words):
    """Собирает числа из последовательности слов (цифры как есть, числительные — свёрткой)."""
    out, cur, used = [], 0, False
    for w in words:
        if re.fullmatch(r"\d+(?:\.\d+)?", w):
            if used:
                out.append(cur); cur, used = 0, False
            out.append(float(w) if "." in w else int(w))
            continue
        v = _ALL_WORDS.get(w)
        if v is None:
            if used:
                out.append(cur); cur, used = 0, False
            continue
        if v >= 100:                       # hundred/thousand — множитель
            cur = (cur or 1) * v
        else:
            cur += v
        used = True
    if used:
        out.append(cur)
    return out


def _operation(words):
    """Определяет действие по фразам ПО ГРАНИЦАМ СЛОВ. '+','-','*','/' или None."""
    norm = " ".join(words)
    ops = set()
    for op, phrases in _OP_PHRASES:
        for p in phrases:
            if re.search(r"\b" + re.escape(p) + r"\b", norm):
                ops.add(op)
                break
    if len(ops) == 1:
        return ops.pop()
    # Ни одного или несколько РАЗНЫХ действий — неоднозначно, не гадаем и не жжём попытку.
    return None


def solve(challenge_text):
    """Ответ на задачу с ДВУМЯ десятичными знаками, или None если неуверенно."""
    words = _segment(_deobfuscate(challenge_text).split())
    nums = _numbers(words)
    op = _operation(words)
    if len(nums) != 2 or op is None:
        return None
    a, b = nums[0], nums[1]
    try:
        if op == "+":
            r = a + b
        elif op == "-":
            r = a - b
        elif op == "*":
            r = a * b
        else:
            if b == 0:
                return None
            r = a / b
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return f"{float(r):.2f}"


if __name__ == "__main__":
    tests = [
        ("A] lO^bSt-Er S[wImS aT/ tW]eNn-Tyy mE^tE[rS aNd] SlO/wS bY^ fI[vE, wH-aTs] ThE/ nEw^ SpE[eD?", "15.00"),
        ("a lobster swims at 30 meters and speeds up by 12 whats the new speed", "42.00"),
        ("the lobster travels 8 meters times 4 reefs", "32.00"),
        ("forty lobsters split evenly over five holes how many each", "8.00"),
        # уроки стресс-теста: обфусцированный глагол + «per second» + служебные слова
        ("A^ l/oOBB-sTer^ Sw]im-mS At TwO[ ME]t[Ers Pe/r S-ec-OnD AnD t]iMee-s 5, WhatS THe^ New SP^e]Ed?", "10.00"),
        ("A lobster swims at eight meters per second and adds 18, whats the new speed?", "26.00"),
        ("To find the answer, a lobster at six slows down by 9, so what is it?", "-3.00"),
        ("For a reef trip the lobster goes 96 and times 37 on the way", "3552.00"),
        # НАСТОЯЩАЯ живая задача 14.09.2026: слова порваны пробелами, буквы удвоены в смешанном регистре
        ("A] lOoO bS t-EeRr S^wIiM s[ aT/ tW]eN tY fIiV e~ cEeMm- EeTtErS/ pEeR sE cOoN d| aNd- aN oThEr/ "
         "rIiV aL+ iInCrEeA sEs/ sEeV eN<, wHaT]s- tHe/ cOoMbIiNeD^ vEeLlOoCiT y?", "32.00"),
        # неоднозначное — обязано молчать
        ("a lobster swims at twenty meters and slows by five then gains three", None),
    ]
    bad = 0
    for ch, expect in tests:
        got = solve(ch)
        ok = got == expect
        bad += not ok
        print(f"  ожидалось {str(expect):<7} получено {str(got):<7} {'OK' if ok else 'ПРОМАХ'}  «{ch[:45]}»")
    raise SystemExit(1 if bad else 0)

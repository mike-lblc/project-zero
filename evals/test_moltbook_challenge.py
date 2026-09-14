"""Решатель проверки Moltbook: главное свойство — НИКОГДА не отвечать уверенно неверно.

Неверный уверенный ответ жжёт попытку (десять подряд — блокировка аккаунта);
молчание (None) безопасно. Поэтому тесты требуют: известные задачи — верно,
неоднозначные и незнакомые — молчание, а стресс-тест в стиле обфускации площадки
(фиксированное зерно) — ноль неверных при высокой доле верных.
"""
import random

import pytest

from core.moltbook_challenge import solve

REAL = "A] lO^bSt-Er S[wImS aT/ tW]eNn-Tyy mE^tE[rS aNd] SlO/wS bY^ fI[vE, wH-aTs] ThE/ nEw^ SpE[eD?"


@pytest.mark.parametrize("text,expect", [
    (REAL, "15.00"),                                                   # пример из skill.md
    ("a lobster swims at 30 meters and speeds up by 12 whats the new speed", "42.00"),
    ("the lobster travels 8 meters times 4 reefs", "32.00"),
    ("forty lobsters split evenly over five holes how many each", "8.00"),
    # уроки стресс-теста 14.09: глагол обфусцирован, «per second», служебные слова
    ("A^ l/oOBB-sTer^ Sw]im-mS At TwO[ ME]t[Ers Pe/r S-ec-OnD AnD t]iMee-s 5, WhatS THe^ New SP^e]Ed?", "10.00"),
    ("A lobster swims at eight meters per second and adds 18, whats the new speed?", "26.00"),
    ("To find the answer, a lobster at six slows down by 9, so what is it?", "-3.00"),
    ("For a reef trip the lobster goes 96 and times 37 on the way", "3552.00"),
    ("a lobster at two hundred fourteen meters per second multiplied by two", "428.00"),
    ("a crab with 1 claw times 2", "2.00"),
    # НАСТОЯЩАЯ живая задача 14.09.2026: слова порваны пробелами, буквы удвоены в смешанном регистре
    ("A] lOoO bS t-EeRr S^wIiM s[ aT/ tW]eN tY fIiV e~ cEeMm- EeTtErS/ pEeR sE cOoN d| aNd- aN oThEr/ "
     "rIiV aL+ iInCrEeA sEs/ sEeV eN<, wHaT]s- tHe/ cOoMbIiNeD^ vEeLlOoCiT y?", "32.00"),
    # слово в семь осколков и настоящие сдвоенные буквы (sixt|e|e|n) — уроки стресс-теста
    ("a cCcRrAb M^ oOvEesS oNn- E/ H]uUnDdD r Eed/ E iI] G H- t^ eEe n AanN^D] th^Ee]N Tt-iI^mEeS^ THreEE.", "354.00"),
    ("sTAaA-Rr[T Aa^ tT f ifFT/ y f^I- Vve. I tT tT iI M[ES[ sSiIx T^E[ eE n . fIin-AL Vv Aa]Ll[ uUu/e?", "880.00"),
    ("Tt H e LlObsS-tE]R[ T]RAaVe-ls nNiIN[E mME tErs^ a-d^dDd[ SsS SsS/ IixXTtT] yY o[ nN^e Rr[eeF^s", "70.00"),
])
def test_known_challenges(text, expect):
    assert solve(text) == expect


@pytest.mark.parametrize("text", [
    "a lobster swims at twenty meters and slows by five then gains three",   # два действия
    "a lobster swims at twenty meters per second, whats the speed",          # нет действия
    "a lobster swims at twenty meters and slows by",                         # одно число
    "a lobster surges past twelve reefs and dwindles near seven",            # незнакомые глаголы
    "the lobster velocity vector at forty crosses nine",                     # незнакомый глагол
    "",
    None,
])
def test_ambiguous_or_unknown_is_silence_not_a_guess(text):
    assert solve(text) is None


# ---------------------------------------------------------------- стресс-тест
_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
          "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
          "seventeen", "eighteen", "nineteen"]
_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty",
         70: "seventy", 80: "eighty", 90: "ninety"}


def _words(n):
    if n < 20:
        return _UNITS[n]
    if n < 100:
        return _TENS[(n // 10) * 10] + (" " + _UNITS[n % 10] if n % 10 else "")
    return _UNITS[n // 100] + " hundred" + (" " + _words(n % 100) if n % 100 else "")


_OPS = {"+": ["speeds up by", "gains", "increases by", "plus", "rises by", "adds"],
        "-": ["slows by", "loses", "decreases by", "minus", "drops by", "slows down by"],
        "*": ["times", "multiplied by"],
        "/": ["divided by", "split evenly among", "shared between"]}
_TEMPLATES = [
    "A lobster swims at {a} meters and {op} {b}, whats the new speed?",
    "{a} lobsters {op} {b} holes how many each",
    "To find the answer, a lobster at {a} {op} {b}, so what is it?",
    "For a reef trip the lobster goes {a} and {op} {b} on the way",
    "In the tide pool a lobster with {a} claws {op} {b}",
    "A lobster swims at {a} meters per second and {op} {b}, whats the new speed?",
]


def _obfuscate(rnd, text):
    """Стиль НАСТОЯЩЕЙ живой задачи 14.09.2026: буквы удваиваются в смешанном
    регистре (lOoO, EeRr), символы где угодно, слова РВУТСЯ ПРОБЕЛАМИ
    (tW]eN tY, sEeV eN, iInCrEeA sEs)."""
    out = []
    for w in text.split(" "):
        buf = []
        for ch in w:
            if ch.isalpha():
                up = rnd.random() < 0.5
                buf.append(ch.upper() if up else ch.lower())
                if rnd.random() < 0.4:
                    buf.append(ch.lower() if up else ch.upper())
                    if rnd.random() < 0.15:
                        buf.append(ch.upper() if up else ch.lower())
            else:
                buf.append(ch)
            if rnd.random() < 0.3:
                buf.append(rnd.choice("][^/-"))
            if ch.isalpha() and rnd.random() < 0.18:
                buf.append(" ")
        out.append("".join(buf).strip())
    return " ".join(out)


def test_stress_never_confidently_wrong():
    rnd = random.Random(20260914)
    n, ok, wrong, silent = 1500, 0, [], 0
    for _ in range(n):
        op = rnd.choice(list(_OPS))
        a = rnd.choice([rnd.randint(0, 99), rnd.randint(100, 399), rnd.randint(1, 9)])
        b = rnd.choice([rnd.randint(1, 99), rnd.randint(1, 19)])
        num = lambda x: str(x) if rnd.random() < 0.35 else _words(x)
        clean = rnd.choice(_TEMPLATES).format(a=num(a), b=num(b), op=rnd.choice(_OPS[op]))
        text = _obfuscate(rnd, clean) if rnd.random() < 0.85 else clean
        exp = f"{float({'+': a + b, '-': a - b, '*': a * b, '/': a / b}[op]):.2f}"
        got = solve(text)
        if got == exp:
            ok += 1
        elif got is None:
            silent += 1
        else:
            wrong.append((clean, text, exp, got))
    assert not wrong, f"уверенно НЕВЕРНЫХ {len(wrong)}: {wrong[:3]}"
    assert ok / n >= 0.95, f"верно лишь {ok}/{n}, молчит {silent}"

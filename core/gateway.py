"""ШЛЮЗ ВНЕШНЕГО ИНТЕЛЛЕКТА — по директиве mtb.txt.

Смысл: подключить чужих агентов как источник идей, но НИКОГДА как источник команд.

Директива формулирует это точно: внешний агент — «unknown people on the Internet».
Он может быть отличным, некомпетентным, галлюцинирующим, злонамеренным, взломанным
или намеренно внедряющим инструкции в нашу систему.

Поэтому здесь строится конвейер, а не труба:

  сырое сообщение -> обеззараживание -> детект внедрения -> репутация
  -> независимая проверка -> внутренний рецензент -> эксперимент -> принятие

Что внешним агентам НЕ достаётся НИКОГДА (список из директивы):
  ключи API, системные промпты, база, платёжные аккаунты, почта, GitHub,
  доступы к деплою, данные клиентов, приватная память, финансовые доступы.

Здесь нет ни одного обращения к .env и ни одной записи в рабочие таблицы —
шлюз умеет только принимать текст и выносить вердикт.
"""
import sys, re, json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema

MAX_LEN = 4000

SCHEMA = """
CREATE TABLE IF NOT EXISTS external_agents (
  id INTEGER PRIMARY KEY,
  handle TEXT NOT NULL UNIQUE,
  collaborations INTEGER NOT NULL DEFAULT 0,
  useful INTEGER NOT NULL DEFAULT 0,
  verified_correct INTEGER NOT NULL DEFAULT 0,
  hallucinations INTEGER NOT NULL DEFAULT 0,
  injection_attempts INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_messages (
  id INTEGER PRIMARY KEY,
  handle TEXT NOT NULL,
  topic TEXT,
  raw TEXT NOT NULL,
  sanitized TEXT,
  verdict TEXT NOT NULL,        -- accepted_as_suggestion | rejected_injection | rejected_low_trust
  flags TEXT,
  received_at TEXT NOT NULL
);
"""

# Признаки попытки превратить сообщение в команду. Список из наблюдаемых приёмов,
# а не из фантазии: каждый шаблон — реальная техника внедрения инструкций.
INJECTION_PATTERNS = [
    (r"\bignore\s+(all\s+)?(previous|prior|above)\b", "просьба игнорировать прежние инструкции"),
    (r"\bdisregard\s+(all\s+)?(previous|prior|your)\b", "просьба отбросить инструкции"),
    (r"забудь\s+(все\s+)?(предыдущие|прошлые)", "просьба забыть инструкции"),
    (r"\byou\s+are\s+now\b", "переопределение роли"),
    (r"\bnew\s+(system\s+)?(prompt|instructions?)\b", "подмена системного промпта"),
    (r"\bsystem\s*:", "имитация системного сообщения"),
    (r"\bact\s+as\s+(if|a\s+)", "подмена роли"),
    (r"\b(reveal|show|print|output)\s+(your\s+)?(system\s+)?(prompt|instructions?|rules)\b",
     "выманивание системного промпта"),
    (r"\b(api[_\s-]?key|secret|token|password|credential|private[_\s-]?key)\b",
     "запрос доступов"),
    (r"\.env\b|\bbrain\.db\b|\bwallet\b.*\b(seed|phrase|private)\b", "запрос секретных файлов"),
    (r"\b(curl|wget|bash|powershell|subprocess|os\.system|eval\()", "просьба выполнить код"),
    (r"\bsend\s+(funds|money|usdc|eth|btc)\b|\btransfer\s+to\b", "просьба перевести средства"),
    (r"\bgit\s+push\b|\bdeploy\b.*\bnow\b", "просьба задеплоить"),
    (r"\bDAN\b|\bjailbreak\b|\bdeveloper\s+mode\b", "известный обход ограничений"),
    (r"\bthis\s+is\s+(an?\s+)?(test|drill|authorized)\b", "ложное разрешение"),
    (r"\bthe\s+(owner|user|human)\s+(said|approved|authorized)\b", "ложная ссылка на владельца"),
]

# Скрытые символы, которыми прячут инструкции от человека, но не от модели
HIDDEN = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


# ---------------------------------------------------------------- 1. обеззараживание
def sanitize(text):
    """Убирает то, чем прячут инструкции, и обрезает до разумного размера."""
    if not isinstance(text, str):
        text = str(text)
    flags = []
    if HIDDEN.search(text):
        flags.append("скрытые управляющие символы")
        # ЗАМЕНА НА ПРОБЕЛ, а не вырезание. Вырезание склеивало слова
        # («кэшированиеignore»), граница слова исчезала и шаблон не срабатывал —
        # то есть скрытые символы работали как обход детектора. Поймано тестом.
        text = HIDDEN.sub(" ", text)
    # разметка, которой имитируют системные блоки
    for tag in ("<system>", "</system>", "<|im_start|>", "<|im_end|>",
                "[INST]", "[/INST]", "<<SYS>>"):
        if tag.lower() in text.lower():
            flags.append(f"имитация служебной разметки: {tag}")
            text = re.sub(re.escape(tag), " ", text, flags=re.I)
    if len(text) > MAX_LEN:
        flags.append(f"обрезано с {len(text)} до {MAX_LEN}")
        text = text[:MAX_LEN]
    return text.strip(), flags


# ---------------------------------------------------------------- 2. детект внедрения
def detect_injection(text):
    """Ищет попытки превратить подсказку в приказ."""
    hits = []
    low = text.lower()
    for pattern, name in INJECTION_PATTERNS:
        if re.search(pattern, low, re.I):
            hits.append(name)
    return hits


# ---------------------------------------------------------------- 3. репутация
def reputation(handle):
    c = _con()
    r = c.execute("SELECT collaborations,useful,verified_correct,hallucinations,injection_attempts "
                  "FROM external_agents WHERE handle=?", (handle,)).fetchone()
    c.close()
    if not r:
        return {"handle": handle, "known": False, "reliability": None,
                "note": "не встречался раньше — доверия нет по умолчанию"}
    total, useful, correct, halluc, inj = r
    rel = round(100 * correct / total, 1) if total else None
    return {"handle": handle, "known": True, "collaborations": total, "useful": useful,
            "verified_correct": correct, "hallucinations": halluc,
            "injection_attempts": inj, "reliability": rel,
            "note": "заблокирован за попытки внедрения" if inj >= 2
                    else ("низкая надёжность" if rel is not None and rel < 30 else "в норме")}


def record_outcome(handle, useful=False, verified=False, hallucinated=False, injected=False):
    """Репутация меняется ТОЛЬКО по проверенным исходам, а не по красноречию."""
    c = _con()
    c.execute("""INSERT INTO external_agents(handle,collaborations,useful,verified_correct,
                 hallucinations,injection_attempts,first_seen,last_seen)
                 VALUES (?,1,?,?,?,?,?,?)
                 ON CONFLICT(handle) DO UPDATE SET
                   collaborations=collaborations+1,
                   useful=useful+?, verified_correct=verified_correct+?,
                   hallucinations=hallucinations+?, injection_attempts=injection_attempts+?,
                   last_seen=?""",
              (handle, int(useful), int(verified), int(hallucinated), int(injected), now(), now(),
               int(useful), int(verified), int(hallucinated), int(injected), now()))
    c.commit(); c.close()


# ---------------------------------------------------------------- 4. приём
def receive(handle, text, topic=None):
    """Единственная точка входа для чужого текста.

    Возвращает ПОДСКАЗКУ для внутреннего рассмотрения — никогда команду.
    Ничего не исполняет, ничего не меняет, ни к чему не даёт доступ.
    """
    raw = text
    clean, flags = sanitize(text)
    inj = detect_injection(clean)
    rep = reputation(handle)

    if inj:
        verdict = "rejected_injection"
        record_outcome(handle, injected=True)
    elif rep.get("injection_attempts", 0) >= 2:
        verdict = "rejected_low_trust"
    elif rep.get("reliability") is not None and rep["reliability"] < 20:
        verdict = "rejected_low_trust"
    else:
        verdict = "accepted_as_suggestion"

    c = _con()
    c.execute("INSERT INTO external_messages(handle,topic,raw,sanitized,verdict,flags,received_at) "
              "VALUES (?,?,?,?,?,?,?)",
              (handle, topic, raw[:2000], clean[:2000], verdict,
               json.dumps(flags + inj, ensure_ascii=False), now()))
    c.commit(); c.close()

    return {
        "verdict": verdict,
        "handle": handle,
        "reputation": rep,
        "sanitize_flags": flags,
        "injection_hits": inj,
        "suggestion": clean if verdict == "accepted_as_suggestion" else None,
        "status": ("ОТКЛОНЕНО: попытка внедрения инструкций" if inj else
                   "ОТКЛОНЕНО: низкое доверие" if verdict == "rejected_low_trust" else
                   "ПРИНЯТО КАК ПОДСКАЗКА — требует независимой проверки перед любым действием"),
    }


def stats():
    c = _con()
    q = lambda s: c.execute(s).fetchone()[0]
    out = {
        "known_agents": q("SELECT COUNT(*) FROM external_agents"),
        "messages": q("SELECT COUNT(*) FROM external_messages"),
        "rejected_injection": q("SELECT COUNT(*) FROM external_messages "
                                "WHERE verdict='rejected_injection'"),
        "rejected_low_trust": q("SELECT COUNT(*) FROM external_messages "
                                "WHERE verdict='rejected_low_trust'"),
        "accepted": q("SELECT COUNT(*) FROM external_messages "
                      "WHERE verdict='accepted_as_suggestion'"),
    }
    c.close()
    return out

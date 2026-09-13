"""НАСТОЯЩИЙ АГЕНТ — не процедура в цикле, а роль, которая рассуждает.

ЧТО ЗДЕСЬ БЫЛО НЕ ТАК ДО ЭТОГО ФАЙЛА. Владелец разобрал систему по восьми
признакам и попал точно. Память, хранение, автомат состояний, права и
исполнение инструментов у нас были настоящие. А вот двух вещей не было
вовсе: у агентов НЕ БЫЛО СИСТЕМНОГО ПРОМПТА и они НЕ РАССУЖДАЛИ — к модели
обращался один модуль из двадцати пяти. Всё остальное было жёстко
запрограммированной последовательностью: «шаг 7 идёт после шага 6».

Такая система выполняет, но не решает. Она не может заметить, что сегодня
разумнее заняться другим, и не может объяснить свой выбор.

ЧТО ТЕПЕРЬ. Агент собирается из восьми частей, и каждая — настоящая:

    РОЛЬ И ПРАВИЛА    системный промпт: кто он, что ему запрещено, что
                      считается для него успехом
    РАССУЖДЕНИЕ       модель смотрит на СВОЁ состояние и выбирает следующее
                      действие из своего же набора инструментов
    ИНСТРУМЕНТЫ       поимённый белый список; вызвать чужой нельзя
    ПАМЯТЬ            своя выборка из базы: недавние прогоны, открытые задачи,
                      что уже пробовал и чем кончилось
    АВТОМАТ СОСТОЯНИЙ задача живёт в core/execution: очередь, работа, блокер,
                      готово — и закрыть её без доказательства нельзя
    СОБЫТИЯ           шина сообщений: вопрос, ответ, передача работы
    ПРАВА             класс действия и суточные пределы из core/guard
    ОТЧЁТНОСТЬ        каждое решение записано вместе с обоснованием и исходом

ГДЕ ПРОХОДИТ ЧЕСТНАЯ ГРАНИЦА. Наш протокол запрещает локальной модели
принимать РЕШЕНИЯ — и это правило не отменяется. Поэтому агент выбирает
только из СВОЕГО БЕЛОГО СПИСКА обратимых действий класса GREEN. Это выбор
внутри заранее одобренного меню, а не свобода делать что угодно. Всё, что
класса YELLOW и выше — публикация, отправка, трата, — уходит на эскалацию
к сильной модели, как и раньше. Агент, который сам себе разрешает
необратимое, называется не автономным, а неуправляемым.
"""
import json
import inspect
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect, ensure_schema, init as init_db  # noqa: E402
from core import guard, router  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_decisions (
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  state_seen TEXT NOT NULL,      -- что агент видел, когда решал
  chose TEXT NOT NULL,           -- выбранный инструмент
  why TEXT,                      -- обоснование его же словами
  allowed INTEGER NOT NULL,      -- прошёл ли выбор проверку прав
  outcome TEXT,                  -- что получилось
  ok INTEGER,
  model TEXT,
  decided_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = init_db()
    ensure_schema(c, SCHEMA)
    # state() читает tasks; на новой базе эта таблица живёт в отдельной схеме
    # execution и раньше первый же оборот агента падал до выбора инструмента.
    from core import execution
    ensure_schema(c, execution.SCHEMA)
    return c


# ═══════════════════════════════════════════════ РЕЕСТР ИНСТРУМЕНТОВ
# Инструмент — это не любая функция, а объявленная возможность: у неё есть
# имя, класс действия и описание для агента. Агент видит ТОЛЬКО описания,
# и вызвать то, чего нет в его списке, не может физически.
TOOLS = {}


@dataclass
class Tool:
    name: str
    action_class: str          # GREEN | YELLOW | RED | BLACK
    describe: str              # что делает — этот текст читает модель
    fn: object = None
    needs: tuple = ()          # чего требует: сеть, gh, модель, сервис
    actor_context: bool = False  # передать имя вызывающего агента первым аргументом


def tool(name, action_class, describe, needs=(), actor_context=False):
    """Объявляет функцию инструментом, доступным агентам."""
    def deco(fn):
        # ПОВТОРНОЕ ИМЯ — ОШИБКА, как и у агентов. Второй инструмент с тем же
        # именем молча затирал первый: агент звал одну функцию, а исполнялась
        # другая. Повторная регистрация ТОЙ ЖЕ функции (перезагрузка модуля)
        # разрешена — это не столкновение.
        old = TOOLS.get(name)
        if old and old.fn is not None and (old.fn.__module__, old.fn.__qualname__) !=                 (fn.__module__, fn.__qualname__):
            raise ValueError(f"инструмент «{name}» уже объявлен в "
                             f"{old.fn.__module__}.{old.fn.__qualname__}; второй с тем же "
                             f"именем затёр бы первый молча")
        TOOLS[name] = Tool(name, action_class, describe, fn, needs, actor_context)
        return fn
    return deco


def _parameters(tool_def):
    """Параметры, которые модель обязана передать выбранному инструменту.

    Имя агента не является данными модели: transport подставляет его сам.
    """
    params = list(inspect.signature(tool_def.fn).parameters.values())
    if tool_def.actor_context and params:
        params = params[1:]
    return [p for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]


def _parse_decision(raw):
    """Достаёт первый настоящий JSON-объект, не полагаясь на хрупкий regex."""
    decoder = json.JSONDecoder()
    text = raw or ""
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


# ═══════════════════════════════════════════════ АГЕНТ
@dataclass
class Agent:
    name: str
    role: str
    system: str                       # системный промпт: роль, правила, успех
    tools: tuple                      # белый список имён инструментов
    kpi: str                          # что для него считается успехом
    memory: tuple = ("runs", "tasks")  # откуда он берёт своё состояние
    max_class: str = "GREEN"          # выше — только через эскалацию
    notes: list = field(default_factory=list)

    # ---------------------------------------------------------- память
    def state(self):
        """Что агент знает о себе прямо сейчас. Только факты из базы."""
        c = _con()          # схема нужна и на чтении: таблица решений могла ещё не существовать
        try:
            recent = [f"{r[0]}: {r[1]}" for r in c.execute(
                "SELECT status, substr(notes,1,110) FROM runs WHERE agent=? "
                "ORDER BY id DESC LIMIT 6", (self.name,))]
            tried = [r[0] for r in c.execute(
                "SELECT DISTINCT chose FROM agent_decisions WHERE agent=? "
                "AND ok=1 ORDER BY id DESC LIMIT 8", (self.name,))]
            failed = [f"{r[0]} -> {r[1]}" for r in c.execute(
                "SELECT chose, substr(outcome,1,70) FROM agent_decisions "
                "WHERE agent=? AND ok=0 ORDER BY id DESC LIMIT 4", (self.name,))]
            observations = [f"{r[0]} -> {r[1]}" for r in c.execute(
                "SELECT chose, substr(outcome,1,300) FROM agent_decisions "
                "WHERE agent=? AND ok=1 ORDER BY id DESC LIMIT 4", (self.name,))]
            tasks = [f"[{r[1]}] {r[0]}" for r in c.execute(
                "SELECT substr(objective,1,80), state FROM tasks "
                "WHERE owner_agent=? AND state IN ('queued','running') "
                "ORDER BY money_proximity DESC LIMIT 5", (self.name,))]
            inbox = [f"от {r[0]}: {r[1][:90]}" for r in c.execute(
                "SELECT sender, body FROM messages WHERE recipient=? "
                "AND consumed_at IS NULL ORDER BY id DESC LIMIT 4", (self.name,))]
        finally:
            c.close()
        # ЧТО МЫ УЖЕ ВЫЯСНЯЛИ ПО ЭТОЙ РОЛИ. Точное совпадение строк этого не
        # находит: тот же вывод, сформулированный иначе, для него новый. Без
        # смыслового поиска агент заново приходит к заключениям, которые
        # система уже сделала, и засчитывает это себе как работу.
        lessons = []
        try:
            from core import recall as _r
            lessons = [f"{h['similarity']}: {h['text'][:120]}"
                       for h in _r.recall(self.kpi, top=3, min_sim=0.55)]
        except Exception:
            lessons = []

        return {"последние прогоны": recent, "что уже срабатывало": tried,
                "что не сработало": failed, "мои задачи": tasks,
                "входящие": inbox, "наблюдения инструментов": observations,
                "что мы уже выясняли": lessons}

    # ---------------------------------------------------------- рассуждение
    def _prompt(self, state):
        rows = []
        for name in self.tools:
            if name not in TOOLS:
                continue
            t = TOOLS[name]
            params = _parameters(t)
            signature = ", ".join(
                p.name + ("" if p.default is inspect.Parameter.empty else " (необязательно)")
                for p in params)
            rows.append(f"  {name}" + (f" [{signature}]" if signature else "")
                        + f" — {t.describe}")
        menu = "\n".join(rows)
        facts = json.dumps(state, ensure_ascii=False, indent=1)
        return (
            f"{self.system}\n\n"
            f"ТВОЙ УСПЕХ ИЗМЕРЯЕТСЯ ТАК: {self.kpi}\n\n"
            f"ЧТО ТЫ МОЖЕШЬ СДЕЛАТЬ ПРЯМО СЕЙЧАС (и ничего другого):\n{menu}\n\n"
            f"ТВОЁ ТЕКУЩЕЕ СОСТОЯНИЕ ПО ФАКТАМ:\n{facts}\n\n"
            f"Выбери ОДНО действие из списка выше. Предпочитай то, что принесёт "
            f"НОВОЕ знание или продвинет работу к деньгам. Не повторяй то, что "
            f"уже срабатывало и не дало нового. Если что-то не сработало — не "
            f"повторяй это без изменения условий.\n\n"
            f"Ответь ТОЛЬКО одной строкой JSON, без пояснений вокруг. Для "
            f"инструмента с параметрами передай их в args; ничего не выдумывай:\n"
            f'{{"tool":"<имя>","args":{{"<параметр>":"<значение>"}},'
            f'"why":"<почему именно это>"}}'
        )

    def decide(self):
        """Агент смотрит на своё состояние и выбирает следующее действие.

        Выбор идёт ТОЛЬКО из белого списка обратимых действий. Это отбор
        внутри одобренного меню, а не свобода: протокол по-прежнему
        запрещает локальной модели принимать решения класса YELLOW и выше.
        """
        st = self.state()
        prompt = self._prompt(st)
        try:
            raw = router.run("classify", prompt)
        except router.EscalationRequired as e:
            return {"tool": None, "why": f"эскалация: {e}", "allowed": False}
        except Exception as e:
            return {"tool": None, "why": f"модель недоступна: {type(e).__name__}",
                    "allowed": False}

        payload = _parse_decision(raw)
        chosen = str(payload.get("tool") or "").strip() or None
        why = str(payload.get("why") or "")
        args = payload.get("args", {})
        if not isinstance(args, dict):
            args = {}

        allowed = bool(chosen) and chosen in self.tools and chosen in TOOLS
        if allowed:
            t = TOOLS[chosen]
            # ПРАВА. max_class — верхняя граница, а не требование точного
            # совпадения. Старое условие запрещало агенту YELLOW даже GREEN и
            # именно поэтому improver падал в облачном цикле.
            rank = {"GREEN": 0, "YELLOW": 1, "RED": 2, "BLACK": 3}
            standing = guard.standing(chosen)
            if (t.action_class == "BLACK" or
                    (rank.get(t.action_class, 99) > rank.get(self.max_class, -1)
                     and not standing)):
                allowed = False
                why = (f"{why} | отклонено: {chosen} класса {t.action_class}, "
                       f"предел агента {self.max_class} и постоянного разрешения нет")
            if allowed:
                params = _parameters(t)
                names = {p.name for p in params}
                required = {p.name for p in params if p.default is inspect.Parameter.empty}
                unexpected = set(args) - names
                missing = required - set(args)
                if unexpected or missing:
                    allowed = False
                    why = (f"{why} | неверные параметры: отсутствуют {sorted(missing)}, "
                           f"лишние {sorted(unexpected)}")
        return {"tool": chosen, "why": why, "allowed": allowed,
                "args": args, "state": st, "raw": (raw or "")[:500]}

    # ---------------------------------------------------------- действие
    def act(self, dry_run=False):
        """Полный оборот агента: посмотреть, решить, проверить права, сделать."""
        d = self.decide()
        chose, why, args = d.get("tool"), d.get("why", ""), d.get("args", {})
        if not d.get("allowed"):
            self._record(d, "не выполнено", ok=False)
            return {"agent": self.name, "chose": chose, "ok": False,
                    "detail": why or "выбор не прошёл проверку"}
        t = TOOLS[chose]
        if dry_run:
            self._record(d, "вхолостую", ok=True)
            return {"agent": self.name, "chose": chose, "ok": True,
                    "detail": "вхолостую", "why": why}
        try:
            guard.check_action(chose, t.action_class)
            out = t.fn(self.name, **args) if t.actor_context else t.fn(**args)
            ok = True
        except Exception as e:
            out, ok = f"{type(e).__name__}: {str(e)[:120]}", False
        self._record(d, str(out)[:300], ok=ok)
        return {"agent": self.name, "chose": chose, "ok": ok,
                "detail": str(out)[:200], "why": why}

    def _record(self, d, outcome, ok, _retry=3):
        """Запись решения НЕ ИМЕЕТ ПРАВА уронить оборот агента.

        Тот же класс ошибки, что уже дважды останавливал систему: работа
        сделана, а процесс падает на попытке о ней рассказать. Потерять
        запись допустимо, потерять работу — нет.
        """
        import sqlite3 as _sq
        import time as _t
        try:
            return self.__record(d, outcome, ok)
        except _sq.OperationalError as e:
            if _retry > 0 and "locked" in str(e).lower():
                _t.sleep(1.5)
                return self._record(d, outcome, ok, _retry - 1)
            print(f"[агент {self.name}] решение не записано ({e}); работа продолжается")
            return None

    def __record(self, d, outcome, ok):
        c = _con()
        c.execute("""INSERT INTO agent_decisions(agent,state_seen,chose,why,allowed,
                     outcome,ok,model,decided_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                  (self.name, json.dumps(d.get("state", {}), ensure_ascii=False)[:1200],
                   d.get("tool") or "-", d.get("why", "")[:400],
                   1 if d.get("allowed") else 0, outcome, 1 if ok else 0,
                   router.model_for("classify"), now()))
        c.commit(); c.close()


# ═══════════════════════════════════════════════ РЕЕСТР АГЕНТОВ
REGISTRY = {}


def register(agent):
    """Вносит агента в состав. ПОВТОРНОЕ ИМЯ — ОШИБКА, а не замена.

    Здесь была тихая потеря. Двое одновременно завели агента с именем
    «executor»: один с одним набором инструментов, другой с другим. Словарь
    молча оставил того, чья строка стояла ниже, — и половина работы исчезла,
    не оставив следа ни в журнале, ни в проверках. Состав при этом выглядел
    исправным: агент есть, промпт есть, инструменты есть.

    Молчаливая перезапись опаснее явного столкновения: столкновение чинят за
    минуту, а пропажу замечают, когда ищут причину, почему способность,
    которую точно добавляли, нигде не срабатывает.
    """
    if agent.name in REGISTRY:
        raise ValueError(
            f"агент «{agent.name}» уже зарегистрирован. Два агента с одним "
            f"именем — это не замена, а потеря: объедините их в одного или "
            f"дайте разные имена.")
    REGISTRY[agent.name] = agent
    return agent


def get(name):
    return REGISTRY.get(name)


def roster():
    """Кто есть, чем вооружён, чем меряется. Для дашборда и для проверки."""
    return [{"name": a.name, "role": a.role, "tools": list(a.tools),
             "kpi": a.kpi, "max_class": a.max_class,
             "prompt_chars": len(a.system)} for a in REGISTRY.values()]

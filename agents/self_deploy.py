"""АГЕНТ МЕНЯЕТ КОД ПЛАТНОГО СЕРВИСА И РАЗВЁРТЫВАЕТ ЕГО САМ (владелец 17.09).

Владелец трижды спросил одно и то же: если Claude выключен, агенты должны уметь ВСЁ,
что делал он, включая правки кода и развёртывание. Я дважды отказывался: агент,
деплоящий платный сервис без надзора, одной ошибкой отдаёт данные бесплатно или роняет
выручку в ноль. Разрешение этого не отменяет — поэтому здесь не «просто деплой», а
деплой, который САМ СЕБЯ ОТКАТЫВАЕТ, если после него сервис перестал продавать.

Порядок, и ни один шаг не пропускается:
 1. Полный набор тестов. Упал хоть один — развёртывания не будет вовсе.
 2. Синтаксис бандла (node --check): бандл, который не парсится, до сети не доходит.
 3. АДРЕС ПОЛУЧАТЕЛЯ И АДРЕСА ВЛАДЕЛЬЦА СВЕРЯЮТСЯ С ЭТАЛОНОМ. Правка, которая
    меняет payTo, не разворачивается никогда — это единственный способ украсть выручку
    целиком, и он закрыт до сети, а не после.
 4. Запоминается ТЕКУЩАЯ живая версия: то, куда мы вернёмся.
 5. Развёртывание.
 6. Живая проверка: каждый платный маршрут отдаёт 402 с НАШИМ адресом, бесплатные —
    200, /health отвечает, расчёт идёт через CDP.
 7. Не сошлось — немедленный откат на запомненную версию и проверка ещё раз.

Ключ Cloudflare берётся из .env (локально) или из окружения (облако). Нет ключа —
шаг честно говорит, что развернуть не может, и ничего не делает.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import bus, guard  # noqa: E402
from core.db import connect  # noqa: E402
from core.identity import SERVICE_URL as SELF  # noqa: E402

WORKER_DIR = ROOT / "worker"
BUNDLE = WORKER_DIR / "src" / "index.js"
UA = "P0-agent/1.0"

# ЭТАЛОН. Эти строки обязаны присутствовать в бандле после любой правки агента.
# Если правка убрала или изменила адрес получателя — это уже не улучшение сервиса.
EXPECTED = {
    "payTo (env var name)": "PAY_TO",
    "owner btc": "bc1qqwgyyqv6raq2jnghals2n2aujgwd4e9p64g4hr",
    "owner tron": "TB9rHqT8yLxwdsWCb3zN2nvjc8wLhsUdaQ",
    "owner sol": "FTbVqWwsfJJ5AuAwNDuCuzdpwCEJahu14HAUgAYcJHuq",
    "owner stx": "SP34GH04YTB01AMXF4CAQ10Y5B7G4E0119N99W986",
}
MIN_PY_TESTS = 297   # текущий набор — 301; падение ниже = сбор сломался
OWNER_EVM_TAIL = "c55354"          # хвост адреса владельца: сверяется в живом 402


def _log_deploy_action(reason):
    """Записать развёртывание в таблицу actions, по которой guard считает предел.

    Без этой строки CAPS["deploy_service"] был числом без силы: check_action
    считает actions, а сюда никто не писал. Класс RED — как и у самого действия.
    Запись не имеет права уронить развёртывание, поэтому ошибки глотаются.
    """
    try:
        c = connect()
        c.execute("CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY, kind TEXT, "
                  "action_class TEXT, dry_run INTEGER, payload TEXT, result TEXT, created_at TEXT)")
        c.execute("INSERT INTO actions(kind,action_class,dry_run,payload,result,created_at) "
                  "VALUES (?,?,?,?,?,?)", ("deploy_service", "RED", 0,
                                          json.dumps({"reason": reason}, ensure_ascii=False)[:500],
                                          "attempt", now()))
        c.commit(); c.close()
    except Exception as e:
        print(f"[self_deploy] действие не записано в actions ({type(e).__name__}); "
              f"предел на этот раз не учтётся", flush=True)


def now():
    return datetime.now(timezone.utc).isoformat()


def _note(agent, claim, conf=None):
    try:
        from agents.worker import note as _wnote
        _wnote(agent, claim, conf=conf)
    except Exception:
        pass


def _table(c):
    c.execute("CREATE TABLE IF NOT EXISTS deploys (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, "
              "from_version TEXT, to_version TEXT, outcome TEXT, detail TEXT)")


def _token():
    tok = os.environ.get("CLOUDFLARE_API_TOKEN") or ""
    if tok:
        return tok
    try:
        from agents.worker import _env_value
        return _env_value("CLOUDFLARE_API_TOKEN")
    except Exception:
        return ""


def _wrangler(args, timeout=300):
    tok = _token()
    if not tok:
        return 1, "нет CLOUDFLARE_API_TOKEN"
    env = dict(os.environ, CLOUDFLARE_API_TOKEN=tok)
    try:
        # shell=True ТОЛЬКО НА WINDOWS, И ЭТО НЕ КОСМЕТИКА.
        #
        # Здесь стояло shell=True со СПИСКОМ аргументов. На Windows это работает:
        # список склеивается в командную строку. На POSIX — нет: получается
        # /bin/sh -c "npx", где "wrangler" становится $0, а "deploy" — $1, то есть
        # ВСЕ аргументы отбрасываются и запускается голый npx.
        #
        # Вот почему облако не развернуло воркер ни разу, хотя шаг стоял в цикле,
        # секрет был передан, node с зависимостями поставлен и предел не исчерпан:
        # мои деплои шли с Windows и проходили, а облачный ubuntu молча запускал
        # npx без команды. Ровно тот случай, когда «работает у меня» и «работает»
        # — разные утверждения.
        #
        # На Windows shell нужен: npx там .cmd, и без shell его не найти.
        r = subprocess.run(["npx", "wrangler"] + list(args), cwd=str(WORKER_DIR), env=env,
                           shell=(os.name == "nt"), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except Exception as e:
        return 1, f"{type(e).__name__}: {str(e)[:160]}"
    return r.returncode, ((r.stdout or "") + (r.stderr or ""))


def _http(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw or "{}")
            except ValueError:
                return r.status, {"_raw": raw[:300]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw or "{}")
        except ValueError:
            return e.code, {"_raw": raw[:300]}
    except Exception as e:
        return 0, {"_err": f"{type(e).__name__}"}


def addresses_intact():
    """Правка не тронула адреса получателя. Проверяется ДО сети."""
    try:
        src = BUNDLE.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"бандл не прочитан: {e}"
    missing = [name for name, needle in EXPECTED.items() if needle not in src]
    if missing:
        return False, "в бандле не найдено: " + ", ".join(missing)
    # Никаких других адресов EVM в списке получателей: одна лишняя строка 0x… в
    # OWNER или PAY_TO — это перевод выручки на чужой кошелёк.
    block = src[src.find("const OWNER = {"):src.find("};", src.find("const OWNER = {")) + 2] if "const OWNER = {" in src else ""
    strays = [a for a in re.findall(r"0x[0-9a-fA-F]{40}", block)]
    if strays:
        return False, f"в OWNER появились адреса EVM: {strays[:3]}"
    return True, "адреса получателя на месте"


def tests_pass():
    """Полный набор — и питоновский, и тесты САМОГО воркера.

    Раньше здесь стоял `unittest discover`: он собирал 174 теста из 230, потому
    что файлы в стиле pytest (фикстуры, monkeypatch) он молча пропускает — «Ran 0
    tests». Гейт, который не видит часть тестов, разрешает деплой на непроверенном
    коде. И отдельно: платный продукт — это воркер, а его собственные тесты
    (prepaid, движок маршрутов) в гейт вообще не входили.
    """
    runs = [
        ("python", [sys.executable, "-X", "utf8", "-m", "pytest", "evals", "-q"], str(ROOT)),
        ("worker", ["node", "--test", "test/*.test.mjs"], str(ROOT / "worker")),
    ]
    parts = []
    for label, cmd, cwd in runs:
        try:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=900, shell=False)
        except FileNotFoundError:
            return False, f"{label}: не найден исполняемый файл {cmd[0]}"
        except Exception as e:
            return False, f"{label}: {type(e).__name__}: {str(e)[:100]}"
        blob = (r.stdout or "") + (r.stderr or "")
        if label == "python":
            m = re.search(r"(\d+) passed", blob)
            n = int(m.group(1)) if m else 0
            # 0 собранных тестов — это не «всё хорошо», это неудачный сбор.
            if r.returncode != 0 or n < MIN_PY_TESTS:
                return False, f"python: {n} тестов (ждём >= {MIN_PY_TESTS}), {blob[-200:].strip()[:160]}"
        else:
            m = re.search(r"# pass (\d+)", blob) or re.search(r"pass (\d+)", blob)
            n = int(m.group(1)) if m else 0
            if r.returncode != 0 or n < 1:
                return False, f"воркер: тесты не прошли — {blob[-200:].strip()[:160]}"
        parts.append(f"{label} {n}")
    return True, ", ".join(parts) + ", ok"


def syntax_ok():
    try:
        r = subprocess.run(["node", "--check", str(BUNDLE)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        return r.returncode == 0, (r.stderr or "ok")[:200]
    except Exception as e:
        return False, f"{type(e).__name__}"


def live_version():
    code, out = _wrangler(["versions", "list"], timeout=240)
    if code:
        return None
    ids = re.findall(r"Version ID:\s+([0-9a-f-]{36})", out)
    return ids[-1] if ids else None


def sells_correctly():
    """Каждый платный маршрут отдаёт 402 с НАШИМ адресом; бесплатные — 200."""
    st, d = _http(SELF + "/prices")
    if st != 200 or not isinstance(d.get("effective"), dict):
        return False, f"/prices отдал {st}"
    bad = []
    for path in d["effective"]:
        s2, body = _http(SELF + path)
        if s2 != 402:
            bad.append(f"{path}={s2}")
            continue
        acc = (body.get("accepts") or [{}])[0]
        if not str(acc.get("payTo", "")).lower().endswith(OWNER_EVM_TAIL):
            bad.append(f"{path}: payTo {acc.get('payTo')}")
    for path in ("/health", "/sample"):
        s3, _ = _http(SELF + path)
        if s3 != 200:
            bad.append(f"{path}={s3}")
    st4, h = _http(SELF + "/health")
    if st4 == 200 and (h.get("settlement") or {}).get("primary") != "cdp":
        bad.append("расчёт идёт не через CDP")
    if bad:
        return False, "; ".join(bad[:6])
    return True, f"{len(d['effective'])} платных отдают 402 с нашим адресом"


def _version_history(limit=8):
    """Идентификаторы версий воркера, новые первыми."""
    code, out = _wrangler(["versions", "list"], timeout=180)
    if code:
        return []
    ids = re.findall(r"Version ID:\s+([0-9a-f-]{36})", out)
    seen, uniq = set(), []
    for v in ids:
        if v not in seen:
            seen.add(v); uniq.append(v)
    return uniq[:limit]


def _known_good_versions(limit=5):
    """Версии, которые КОГДА-ТО прошли проверку продаж. Свежие первыми."""
    try:
        c = connect(); _table(c)
        rows = c.execute("SELECT to_version FROM deploys WHERE outcome='ok' AND to_version IS NOT NULL "
                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        c.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def _rollback_to_health(before, max_steps=4):
    """Откат — это не один шаг назад.
    
    Первый прогон отката сел на предыдущую версию и остался сломанным: та версия
    БЫЛА тем же неудачным деплоем. Шаг назад бесполезен, если яма начинается на
    шаг назад. Поэтому шагаем дальше, пока сервис не начнёт продавать, и в первую
    очередь — на версии, которые уже проходили проверку продаж.
    """
    candidates = []
    for v in [before] + _known_good_versions() + _version_history():
        if v and v not in candidates:
            candidates.append(v)
    last = "нет версий для отката"
    for v in candidates[:max_steps]:
        rb_code, rb_out = _wrangler(["rollback", v, "--message", "auto-rollback: service stopped selling"],
                                    timeout=420)
        if rb_code:
            last = f"откат на {v} не прошёл: {rb_out[-120:]}"
            continue
        time.sleep(12)
        ok, detail = sells_correctly()
        if ok:
            return True, f"{detail} (откат на {v})", v
        last = f"{v}: {detail}"
    return False, f"откат не вернул продажи — {last}", None


def _verify_settled(attempts=3, first_wait=12, gap=6):
    """Проверка после разноса версии: единогласно и не раньше времени."""
    time.sleep(first_wait)
    last = ""
    for i in range(attempts):
        ok, detail = sells_correctly()
        if not ok:
            return False, f"(проверка {i + 1}/{attempts}) {detail}"
        last = detail
        if i + 1 < attempts:
            time.sleep(gap)
    return True, f"{last}; подтверждено {attempts} раза подряд"


def _bundle_hash():
    """Хеш того, что уезжает в сеть: исходник воркера И объявленные маршруты.

    routes.json тоже едет в бандл (воркер запекает его при загрузке), поэтому
    новый платный маршрут, записанный туда при исчерпанном KV, обязан считаться
    изменением — иначе деплой его не заметит и маршрут не появится в сети никогда.
    """
    import hashlib
    h = hashlib.sha256()
    for f in (BUNDLE, WORKER_DIR / "routes.json"):
        try:
            h.update(f.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:16]


def _behind_origin():
    """Отстаёт ли рабочая копия от origin/main. Возвращает (отстаёт, почему).

    Неизвестность трактуется как «не отстаём»: git может быть недоступен, и это не
    повод запретить деплой вовсе. Но если отставание ВИДНО — деплой отменяется.
    """
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                           text=True, timeout=60)
        local = (r.stdout or "").strip()
        subprocess.run(["git", "fetch", "--quiet", "origin", "main"], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=180)
        r2 = subprocess.run(["git", "rev-parse", "origin/main"], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=60)
        remote = (r2.stdout or "").strip()
        if not local or not remote or local == remote:
            return False, "копия совпадает с origin/main"
        r3 = subprocess.run(["git", "merge-base", "--is-ancestor", local, remote],
                            cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        if r3.returncode == 0:
            return True, f"копия {local[:8]} отстаёт от origin/main {remote[:8]}"
        return False, "копия не является предком origin/main (своя ветка правок)"
    except Exception as e:
        return False, f"состояние git не выяснено ({type(e).__name__}) — не запрещаем деплой"


def _fast_forward():
    """Подтянуть origin/main БЕЗ слияний. Возвращает (получилось, как именно).

    Только fast-forward и намеренно: он не создаёт коммитов слияния, не трогает
    чужие правки и честно падает, если история разошлась или в рабочей копии есть
    несохранённое. В облаке копия чистая, поэтому обычно проходит; на машине
    владельца с незакоммиченными правками — откажется, и это правильно.
    """
    try:
        f = subprocess.run(["git", "fetch", "--quiet", "origin", "main"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=240)
        if f.returncode:
            return False, f"fetch не прошёл: {(f.stderr or '')[-120:]}"
        m = subprocess.run(["git", "merge", "--ff-only", "origin/main"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=240)
        if m.returncode:
            return False, f"fast-forward невозможен: {((m.stderr or '') + (m.stdout or ''))[-140:]}"
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=60)
        return True, f"подтянуто до {(head.stdout or '').strip()}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:100]}"


def deploy_if_changed():
    """Развернуть ТОЛЬКО если исходник воркера изменился с прошлого деплоя.

    Это то, что делает self_deploy пригодным для цикла. Разворачивать на КАЖДОМ
    обороте нельзя: это жгло бы суточный предел, гоняло бы разнос версии впустую и
    рисковало бы откатами на ровном месте. Здесь шаг сравнивает хеш бандла с хешем
    последнего успешного деплоя: совпал — тихий no-op; изменился (кто-то залил
    правку в main, и облако её выкачало) — полный безопасный деплой с проверкой и
    откатом. Так агенты САМИ выкатывают изменение, когда оно есть, и молчат, когда
    его нет — без человека у пульта.
    """
    # Нет ключа Cloudflare — деплой физически невозможен. Выходим ДО записи
    # действия и попытки, иначе облако жгло бы суточный предел на заведомо
    # провальных попытках, пока владелец не добавит секрет CLOUDFLARE_API_TOKEN.
    if not _token():
        return "нет ключа Cloudflare (секрет CLOUDFLARE_API_TOKEN не задан) — деплой в облаке пропущен"
    # СТАРЫМ КОДОМ ПОВЕРХ НОВОГО — НИКОГДА.
    #
    # Деплоить могут двое: локальная машина и облако. Облако выкачивает main при
    # СТАРТЕ прогона и живёт пять часов, поэтому к середине прогона его копия
    # может отставать. Замеренный случай: облако держало 64907f6 (11 маршрутов),
    # а в сети уже стояли 15 из свежего коммита. Разверни оно свою копию — и
    # четыре платных маршрута тихо исчезли бы, причём откат НЕ сработал бы:
    # одиннадцать маршрутов честно отдают 402, проверка продаж прошла бы.
    behind, why = _behind_origin()
    if behind:
        # ОТСТАЁШЬ — ПОДТЯНИСЬ, А НЕ СДАВАЙСЯ.
        #
        # Предохранитель «не разворачивать старое поверх нового» правильный, но
        # первая его версия просто отказывалась. Следствие замерено: облачный
        # прогон выкачивает main на старте и живёт пять часов, а я за вечер
        # запушил ещё три коммита — значит копия устаревала через минуты, и
        # облако не разворачивало НИКОГДА, пока человек работает. Автономность,
        # существующая только когда человек остановился, — не автономность.
        #
        # Цель предохранителя — «в сети должен оказаться свежий код», и она
        # достигается не отказом, а обновлением. Тянем только fast-forward: он
        # не создаёт слияний и честно падает при расхождении, и тогда мы всё-таки
        # отказываемся.
        pulled, how = _fast_forward()
        if not pulled:
            return f"деплой пропущен: {why}; подтянуть не удалось ({how})"
        behind, why = _behind_origin()
        if behind:
            return f"деплой пропущен: {why} даже после обновления ({how})"
    cur = _bundle_hash()
    if not cur:
        return "бандл не прочитан — деплой не нужен"
    try:
        c = connect(); _table(c)
        c.execute("CREATE TABLE IF NOT EXISTS deploy_hash (id INTEGER PRIMARY KEY, hash TEXT, at TEXT)")
        row = c.execute("SELECT hash FROM deploy_hash ORDER BY id DESC LIMIT 1").fetchone()
        c.close()
    except Exception as e:
        return f"состояние деплоя не прочитано ({type(e).__name__}) — пропускаю во избежание лишнего деплоя"
    last = row[0] if row else None
    if last == cur:
        return f"бандл не менялся (hash {cur}) — деплой не нужен"
    result = deploy(reason=f"bundle changed {last or 'nil'}→{cur}")
    # Отмечаем хеш только при успешном деплое — иначе следующий оборот повторит.
    if "развёрнуто и проверено" in result:
        try:
            c = connect()
            c.execute("CREATE TABLE IF NOT EXISTS deploy_hash (id INTEGER PRIMARY KEY, hash TEXT, at TEXT)")
            c.execute("INSERT INTO deploy_hash(hash,at) VALUES (?,?)", (cur, now()))
            c.commit(); c.close()
        except Exception:
            pass
    return result


def deploy(reason="agent change"):
    """Развернуть текущее дерево. Не сошлось после — немедленный откат."""
    guard.check_action("deploy_service", "RED")
    checks = []
    ok, why = addresses_intact()
    checks.append(f"адреса: {why}")
    if not ok:
        _note("improver", f"РАЗВЁРТЫВАНИЕ ОТМЕНЕНО: {why}. Правка, меняющая адрес получателя, "
                          f"не разворачивается никогда.", conf=1.0)
        return "ОТМЕНЕНО — " + why
    ok, why = syntax_ok()
    checks.append(f"синтаксис: {why[:60]}")
    if not ok:
        return "ОТМЕНЕНО — бандл не парсится: " + why[:160]
    ok, why = tests_pass()
    checks.append(f"тесты: {why}")
    if not ok:
        _note("improver", f"РАЗВЁРТЫВАНИЕ ОТМЕНЕНО: {why}. Пока тесты красные, платный сервис "
                          f"не трогаем.", conf=1.0)
        return "ОТМЕНЕНО — " + why

    before = live_version()
    # СЧИТАЕМ РАЗВЁРТЫВАНИЕ ДО СЕТИ, ЧТОБЫ ПРЕДЕЛ БЫЛ НАСТОЯЩИМ.
    #
    # guard.check_action только СЧИТАЕТ строки в таблице actions, но deploy_service
    # туда никто не писал — значит суточный предел CAPS["deploy_service"]=4 не
    # срабатывал никогда (адверсарный аудит поймал это на CAPS в целом). Пишем
    # строку здесь, в момент реальной попытки развернуть: пятое за сутки упрётся
    # в предел на guard.check_action следующего вызова. Пишем ДО _wrangler, чтобы
    # и упавшее развёртывание считалось попыткой — иначе предел обходится сбоями.
    _log_deploy_action(reason)
    code, out = _wrangler(["deploy"], timeout=420)
    if code:
        return f"развернуть не удалось: {out[-200:]}"
    after = None
    m = re.search(r"Current Version ID:\s+([0-9a-f-]{36})", out)
    if m:
        after = m.group(1)

    # ПРОВЕРЯТЬ СРАЗУ — ЗНАЧИТ ПРОВЕРИТЬ СТАРУЮ ВЕРСИЮ.
    #
    # Первый прогон этой защиты отрапортовал «развёрнуто и проверено» на сервисе,
    # где я НАМЕРЕННО сломал /count: проверка успела пройти до того, как Cloudflare
    # разнёс новую версию по краю, то есть измерила предыдущую. Сеть, которая
    # проверяет слишком рано, не сеть вовсе.
    #
    # Поэтому: пауза на разнос, затем ТРИ проверки подряд с интервалом, и годным
    # считается только единогласный результат. Настоящая поломка провалит все три,
    # а запаздывающий край — не выдаст ложного «всё хорошо».
    good, detail = _verify_settled()
    c = connect(); _table(c)
    if good:
        c.execute("INSERT INTO deploys(at,from_version,to_version,outcome,detail) VALUES (?,?,?,?,?)",
                  (now(), before, after, "ok", f"{reason}; {detail}"))
        c.commit(); c.close()
        _note("improver", f"АГЕНТ РАЗВЕРНУЛ СЕРВИС САМ: {reason}. Проверки до сети: "
                          + "; ".join(checks) + f". После: {detail}. Версия {after}.", conf=0.95)
        return f"развёрнуто и проверено: {detail} (версия {after})"

    # НЕ СОШЛОСЬ — ОТКАТ.
    good2, detail2, landed = _rollback_to_health(before)
    c.execute("INSERT INTO deploys(at,from_version,to_version,outcome,detail) VALUES (?,?,?,?,?)",
              (now(), before, after, "rolled_back",
               f"{reason}; сломалось: {detail}; после отката: {detail2}"))
    c.commit(); c.close()
    _note("improver", f"РАЗВЁРТЫВАНИЕ ОТКАЧЕНО АВТОМАТИЧЕСКИ. После деплоя сервис перестал "
                      f"продавать: {detail}. Села версия: {landed or 'здоровой не нашлось'}; "
                      f"сейчас: {detail2}", conf=1.0)
    bus.broadcast("improver", f"Откатил своё же развёртывание: сервис перестал продавать ({detail}). "
                              f"Сейчас: {detail2}")
    return f"ОТКАЧЕНО — после деплоя: {detail}; после отката: {detail2}"


def history(limit=5):
    c = connect(); _table(c)
    rows = c.execute("SELECT at, outcome, detail FROM deploys ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    if not rows:
        return "развёртываний агентом ещё не было"
    return "; ".join(f"{str(r[0])[:19]} {r[1]}" for r in rows)

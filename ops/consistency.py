"""ДЕТЕКТОРЫ КЛАССОВ ОШИБОК, которые прежде находил только человек.

Владелец справедливо спросил: почему адверсарий (проверяющий на ложь) и
механик/улучшатель (чинящий код) не поймали и не починили эти дефекты сами?
Честный ответ: они ловят ИЗВЕСТНЫЕ образцы и нарушенные инварианты. У них не
было ПРОВЕРКИ на те классы, что всплыли за эти дни:

  * устаревший источник     лиды читали каталог трёхдневной давности, пока
                            свежий срез рос — «0 новых лидов» выглядело нормой;
  * замороженный вывод      hot_leads жёстко резал топ-25, и число лидов стояло
                            на месте, хотя источник вырос втрое;
  * сказано против данных    дашборд/отчёт/карта утверждали одно, база показывала
                            другое (платежей 0 при непустой таблице, и т.п.);
  * мёртвый канал при живом  find_channel смотрел только главную страницу и не
    обходном пути           видел репозиториев, которые находит поиск GitHub.

Проверка ловит то, на что у неё есть проверка. Эти детекторы расширяют сеть на
перечисленные классы: находки становятся событиями invariant_broken, и тогда
адверсарий с механиком получают их вне очереди — то есть система начинает
находить сама то, что раньше находил только человек.

Автопочинку это НЕ делает и делать не должно: правку кода по живым данным
применяет человек-уровневый разум, а локальная модель на 30 млрд параметров
намеренно ограничена (не трогает ядро, откатывается при просадке аудита). Роль
детекторов — не молчать, а поднять флаг с доказательством.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect  # noqa: E402


def _n(c, sql, default=0):
    try:
        r = c.execute(sql).fetchone()
        return r[0] if r and r[0] is not None else default
    except Exception:
        return default


def stale_sources():
    """Источник, который читают, СИЛЬНО старше свежего среза того же каталога.

    Класс бага: один процесс обновляет файл А, другой читает файл Б с теми же
    данными, и Б отстаёт. Снаружи это выглядит как «данных нет», а не «читаем
    старое». Здесь известные пары каталога сверяются по времени изменения.
    """
    findings = []
    pairs = [(ROOT / "worker" / "catalog.slim.json", ROOT / "data" / "bazaar_index.json",
              "каталог x402")]
    for fresh, consumed, name in pairs:
        if fresh.exists() and consumed.exists():
            drift = fresh.stat().st_mtime - consumed.stat().st_mtime
            if drift > 12 * 3600:
                findings.append({
                    "класс": "устаревший источник",
                    "что": f"{name}: {consumed.name} старше свежего {fresh.name} на "
                           f"{drift / 3600:.0f} ч",
                    "почему плохо": "потребитель читает устаревшие данные; это выглядит "
                                    "как отсутствие новизны, а не как чтение старого"})
    return findings


def frozen_output():
    """Метрика сильно ниже того, что позволяет её источник — признак потолка.

    Класс бага: жёсткий предел (LIMIT 25) молча морозит вывод, и число стоит на
    месте, хотя источник вырос. Здесь число лидов сравнивается с числом
    операторов с платящими в свежем каталоге — если лидов кратно меньше
    доступного, это заморозка.
    """
    findings = []
    try:
        import json
        slim = ROOT / "worker" / "catalog.slim.json"
        available = 0
        if slim.exists():
            hosts = {}
            for r in json.loads(slim.read_text(encoding="utf-8")):
                if int(r.get("y") or 0) >= 5:
                    h = (r.get("u") or "").split("//")[-1].split("/")[0]
                    if h:
                        hosts[h] = 1
            available = len(hosts)
        c = connect()
        leads = _n(c, "SELECT COUNT(*) FROM leads")
        c.close()
        if available and leads < available * 0.3 and available - leads > 40:
            findings.append({
                "класс": "замороженный вывод",
                "что": f"лидов {leads}, а платящих операторов в свежем каталоге {available}",
                "почему плохо": "вывод кратно ниже доступного — вероятен жёсткий потолок "
                                "или устаревший источник; покупатели не заводятся"})
    except Exception as e:
        findings.append({"класс": "замороженный вывод", "что": f"проверка не выполнена: {type(e).__name__}",
                         "почему плохо": "детектор должен работать; молчание — не результат"})
    return findings


def say_vs_data():
    """Что система УТВЕРЖДАЕТ против того, что показывают ДАННЫЕ.

    Класс бага: дашборд/отчёт/карта говорят одно, база — другое. Много правок
    за эти дни были именно такими: «платежей 0» при данных в другой таблице,
    состав 24 против 18 и т.п. Здесь сверяются несколько пар утверждение↔факт.
    """
    findings = []
    c = connect()
    try:
        # 1. карта возможностей должна совпадать с базой по числу исполнимых услуг
        try:
            from core.capabilities import capability_map
            m = capability_map()
            claimed = len(m.get("услуги исполнимы") or [])
        except Exception:
            claimed = None
        real = _n(c, "SELECT COUNT(*) FROM services WHERE status='executable'")
        if claimed is not None and claimed != real:
            findings.append({"класс": "сказано против данных",
                             "что": f"карта: исполнимых услуг {claimed}, база: {real}",
                             "почему плохо": "агенты видят неверную картину возможностей"})
        # 2. дашборд считает платежи из payment_receipts — там и payments не должны расходиться вслепую
        recs = _n(c, "SELECT COUNT(*) FROM payment_receipts")
        legacy = _n(c, "SELECT COUNT(*) FROM payments")
        if recs != legacy:
            findings.append({"класс": "сказано против данных",
                             "что": f"поступлений в payment_receipts {recs}, в payments {legacy}",
                             "почему плохо": "две таблицы платежей разошлись — дашборд покажет не то"})
        # 3. проверенных маршрутов оплаты быть больше нуля (иначе принять нечего)
        routes = _n(c, "SELECT COUNT(*) FROM payment_routes WHERE status='verified'")
        if routes == 0:
            findings.append({"класс": "сказано против данных",
                             "что": "проверенных маршрутов оплаты 0",
                             "почему плохо": "принять деньги физически некуда"})
    finally:
        c.close()
    return findings


def unreachable_but_findable():
    """Много лидов без канала, хотя второй метод (поиск GitHub) их находит.

    Класс бага: канал ищется одним узким способом (ссылка на главной), а более
    широкий (поиск репозитория по бренду) не задействован — контактируемых
    лидов кажется мало, хотя они есть.
    """
    findings = []
    c = connect()
    try:
        checked = _n(c, "SELECT COUNT(*) FROM leads WHERE reachable IS NOT NULL")
        with_ch = _n(c, "SELECT COUNT(*) FROM leads WHERE reachable=1")
        total = _n(c, "SELECT COUNT(*) FROM leads")
        unchecked = total - checked
        # Считаем по ПРОВЕРЕННЫМ: непроверенный лид — это «ещё не смотрели»,
        # а не «канала нет». Флаг только если среди проверенных доля с каналом мала.
        if checked > 40 and with_ch / max(checked, 1) < 0.15:
            findings.append({"класс": "мёртвый канал при живом обходном пути",
                             "что": f"из {checked} проверенных лидов канал есть лишь у {with_ch}",
                             "почему плохо": "поиск канала слаб — второй метод (репозиторий "
                                             "по бренду через GitHub) находит больше"})
        elif unchecked > 200 and checked < total * 0.3:
            findings.append({"класс": "мёртвый канал при живом обходном пути",
                             "что": f"не проверено каналов у {unchecked} лидов из {total}",
                             "почему плохо": "воронка не наполняется: find_channel не успевает "
                                             "обойти новых лидов — поднять частоту/лимит шага"})
    finally:
        c.close()
    return findings


def claimed_refresh_vs_files():
    """Журнал говорит «обновлено», а файлы не менялись.

    Класс бага: шаг рапортует об обновлении, ничего не записывая. refresh_market
    четыре дня писал «market refreshed» каждые десять минут при файлах каталога
    от 10.09 — детектор «устаревший источник» молчал, потому что сравнивал два
    одинаково старых файла друг с другом, а не с тем, что утверждает журнал.
    """
    findings = []
    c = connect()
    try:
        last = _n(c, "SELECT MAX(started_at) FROM runs WHERE notes LIKE "
                     "'refresh_market: market refreshed%'", None)
    finally:
        c.close()
    slim = ROOT / "worker" / "catalog.slim.json"
    if not last or not slim.exists():
        return findings
    from datetime import datetime, timezone
    try:
        claimed = datetime.fromisoformat(str(last)).timestamp()
    except ValueError:
        return findings
    written = slim.stat().st_mtime
    if claimed - written > 24 * 3600:
        findings.append({
            "класс": "сказано против данных",
            "что": f"журнал: «market refreshed» {str(last)[:16]}, а файл каталога записан "
                   f"{datetime.fromtimestamp(written, timezone.utc):%Y-%m-%d %H:%M} UTC",
            "почему плохо": "шаг рапортует об обновлении, не записывая его — все потребители "
                            "каталога (лиды, обращения, разведка) читают старое, считая свежим"})
    return findings


DETECTORS = [stale_sources, frozen_output, say_vs_data, unreachable_but_findable,
             claimed_refresh_vs_files]


def scan():
    """Все детекторы. Возвращает список находок."""
    out = []
    for d in DETECTORS:
        try:
            out.extend(d())
        except Exception as e:
            out.append({"класс": d.__name__, "что": f"детектор упал: {type(e).__name__}",
                        "почему плохо": "детектор, который сам падает, хуже отсутствующего"})
    return out


def raise_events(findings):
    """Каждую находку — событием invariant_broken, чтобы адверсарий получил её вне очереди."""
    if not findings:
        return 0
    try:
        from core import events
    except Exception:
        return 0
    n = 0
    for f in findings:
        try:
            events.publish("invariant_broken",
                           {"name": f"consistency:{f['класс']}", "detail": f["что"]},
                           source="consistency")
            n += 1
        except Exception:
            pass
    return n


if __name__ == "__main__":
    found = scan()
    print("=" * 72)
    print("СВЕРКА СОГЛАСОВАННОСТИ — классы, что раньше находил только человек")
    print("=" * 72)
    if not found:
        print("  расхождений не найдено")
    for f in found:
        print(f"  [{f['класс']}] {f['что']}")
        print(f"        {f['почему плохо']}")
    print("=" * 72)
    print(f"ИТОГ: находок {len(found)}")

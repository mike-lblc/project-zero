"""P0 spine. The database IS the asset - every external platform is a replaceable pipe."""
import sqlite3, os, re, json, time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "brain.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ============ EVIDENCE LAYER: no source row, no vote ============
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  title TEXT,
  fetched_at TEXT NOT NULL,
  content_hash TEXT,
  raw_excerpt TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY,
  claim TEXT NOT NULL,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  agent TEXT NOT NULL,
  created_at TEXT NOT NULL,
  confidence REAL,
  disputed_by TEXT
);

-- ============ CANDIDATES + RUBRIC ============
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  model TEXT,                 -- business model description
  status TEXT NOT NULL DEFAULT 'open',   -- open | eliminated | shortlisted | chosen
  eliminated_by_gate TEXT,    -- G1..G6 that killed it
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scores (
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id),
  criterion TEXT NOT NULL,
  value REAL,
  evidence_ids TEXT NOT NULL,  -- JSON list; empty = INVALID score
  agent TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- ============ COUNCIL (DECISION_PROTOCOL.md) ============
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY,
  action_class TEXT NOT NULL,        -- GREEN | YELLOW | RED | BLACK
  summary TEXT NOT NULL,
  payload TEXT,
  falsifier TEXT NOT NULL,           -- "how we would know this failed" - required
  evidence_ids TEXT,
  agent TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed'  -- proposed|vetoed|approved|executed|rejected
);
CREATE TABLE IF NOT EXISTS objections (
  id INTEGER PRIMARY KEY,
  proposal_id INTEGER NOT NULL REFERENCES proposals(id),
  agent TEXT NOT NULL,
  severity TEXT NOT NULL,            -- blocking | concern
  argument TEXT NOT NULL,
  evidence_ids TEXT,
  created_at TEXT NOT NULL,
  proved_correct INTEGER              -- filled later from outcomes -> reputation
);
CREATE TABLE IF NOT EXISTS rulings (
  id INTEGER PRIMARY KEY,
  proposal_id INTEGER NOT NULL REFERENCES proposals(id),
  decision TEXT NOT NULL,            -- approve | reject | defer_to_owner
  addressed_objection_id INTEGER REFERENCES objections(id),  -- must address strongest
  reasoning TEXT NOT NULL,
  model_used TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- ============ AGENT BUS (agents talk here - free, local) ============
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  sender TEXT NOT NULL,
  recipient TEXT,                    -- NULL = broadcast
  topic TEXT,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  consumed_at TEXT
);

-- ============ ACTIONS + BLAST RADIUS ============
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY,
  proposal_id INTEGER REFERENCES proposals(id),
  kind TEXT NOT NULL,
  action_class TEXT NOT NULL,
  dry_run INTEGER NOT NULL DEFAULT 1,
  payload TEXT,
  result TEXT,
  created_at TEXT NOT NULL
);

-- ============ THE ASSET: our own list copy ============
CREATE TABLE IF NOT EXISTS subscribers (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  esp_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  source TEXT,
  consent_proof TEXT,
  segment TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS email_events (
  id INTEGER PRIMARY KEY,
  subscriber_id INTEGER REFERENCES subscribers(id),
  campaign TEXT,
  event TEXT NOT NULL,               -- sent|open|click|bounce|unsub
  occurred_at TEXT NOT NULL,
  meta TEXT
);

-- ============ GROUND TRUTH: revenue ============
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY,
  chain TEXT NOT NULL,
  tx_hash TEXT NOT NULL UNIQUE,
  amount TEXT NOT NULL,
  asset TEXT NOT NULL,
  received_at TEXT NOT NULL,
  attributed_to TEXT
);

-- ============ LEARNING: agent reputation from OUTCOMES only ============
CREATE TABLE IF NOT EXISTS agent_reputation (
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  role TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0,
  correct INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  UNIQUE(agent, role)
);

-- ============ ЧЕСТНОСТЬ ДОКАЗАТЕЛЬСТВА ============
-- Эти две таблицы раньше создавались отдельным скриптом и отсутствовали
-- при развёртывании с нуля. Обнаружено прогоном на серверах GitHub.
CREATE TABLE IF NOT EXISTS human_interventions (
  id INTEGER PRIMARY KEY,
  what TEXT NOT NULL,
  why TEXT NOT NULL,
  minutes REAL,
  category TEXT NOT NULL,          -- account_creation | key_paste | approval | rescue | other
  agent_could_have INTEGER NOT NULL DEFAULT 0,
  occurred_at TEXT NOT NULL
);
-- Обязана оставаться ПУСТОЙ, иначе заявление «с нуля» недействительно
CREATE TABLE IF NOT EXISTS spend (
  id INTEGER PRIMARY KEY,
  amount TEXT NOT NULL,
  currency TEXT NOT NULL,
  what TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT,
  notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_unconsumed ON messages(recipient, consumed_at);
CREATE INDEX IF NOT EXISTS idx_ev_source ON evidence(source_id);
"""

_WAL_SET = False


def connect():
    """Соединение с базой.

    busy_timeout обязателен: агенты работают одновременно, и без ожидания
    второй пишущий получает «database is locked» и падает. Поймано на живой
    системе — охотник за баунти не смог записать находки, пока воркер крутил цикл.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    # Режим журнала переключается ОДИН РАЗ на процесс. Смена journal_mode берёт
    # исключительную блокировку, а выполнялась она при каждом открытии — десятки
    # раз в минуту. Пока воркер писал, любой параллельный аудит получал
    # «database is locked» на ровном месте, и один такой отказ убивал воркер.
    global _WAL_SET
    if not _WAL_SET:
        try:
            con.execute("PRAGMA journal_mode=WAL")
            _WAL_SET = True
        except sqlite3.OperationalError:
            pass          # уже WAL или кто-то пишет — не повод падать
    con.row_factory = sqlite3.Row
    return con

_SCHEMA_DONE = set()


def ensure_schema(con, schema_sql):
    """Создаёт таблицы И ДОБАВЛЯЕТ недостающие колонки в уже существующие.

    ВЫПОЛНЯЕТСЯ ОДИН РАЗ НА ПРОЦЕСС для каждой схемы. Это не оптимизация, а
    исправление того, что роняло всю экосистему: executescript с CREATE TABLE
    берёт в SQLite пишущую блокировку, и он вызывался при КАЖДОМ открытии
    соединения — сотни раз в минуту у полутора десятков агентов. Достаточно
    было параллельно запустить аудит, чтобы воркер получил «database is locked»
    и умер целиком. Схема за время работы процесса не меняется, поэтому
    проверять её каждый раз не нужно.

    Зачем это отдельно от executescript. `CREATE TABLE IF NOT EXISTS` на
    существующей таблице не делает НИЧЕГО — включая случай, когда в схему
    добавили новую колонку. Именно так охотник за баунти перестал сохранять
    находки: колонку payout вписали в схему, таблица осталась старой, и каждый
    заход падал на вставке. Снаружи это выглядело как «работы не нашлось».

    Первичные ключи и UNIQUE через ALTER TABLE не добавить — такие колонки
    пропускаются молча: они есть только в свежих таблицах, а это уже не
    молчаливая потеря данных, а разница в ограничениях.
    """
    key = hash(schema_sql)
    if key in _SCHEMA_DONE:
        return con
    # ПО ОДНОМУ ОПЕРАТОРУ, А НЕ executescript.
    #
    # executescript в Python начинает с неявного COMMIT и НЕ УВАЖАЕТ
    # busy_timeout: при занятой базе он падает сразу, не дожидаясь. Отсюда
    # брались «database is locked» даже после того, как ожидание подняли до
    # шестидесяти секунд, — и один такой отказ убивал воркер целиком.
    # Обычный execute ожидание уважает, поэтому разбираем скрипт на операторы.
    # Границы операторов определяет сам sqlite: разбиение по «;» спотыкается о
    # комментарии и отдаёт обрывки, на которых драйвер говорит «incomplete input».
    def _strip_comments(text):
        """Убирает ведущие строки-комментарии.

        Без этого оператор, перед которым стоит комментарий, начинался с «--»
        и ОТБРАСЫВАЛСЯ ЦЕЛИКОМ. Локально это было незаметно — база уже
        существовала. На чистой установке пропадала половина таблиц, и первый
        же облачный прогон упал на создании индекса по несуществующей таблице.
        Ошибка, которую видно только там, где ещё ничего нет.
        """
        lines = [ln for ln in text.splitlines()
                 if ln.strip() and not ln.strip().startswith("--")]
        return "\n".join(lines).strip()

    def _exec(stmt, tries=5):
        """Создание таблицы ждёт освобождения базы, а не падает.

        busy_timeout действует на обычные запросы, но создание таблицы при
        занятой базе всё равно может отказать сразу. Агент, который не смог
        завести себе таблицу решений только потому, что воркер в этот момент
        писал строку, выглядит как сломанный агент. Ждём и повторяем.
        """
        for i in range(tries):
            try:
                con.execute(stmt)
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or i == tries - 1:
                    raise
                time.sleep(1 + i)

    buf = ""
    for line in schema_sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = _strip_comments(buf)
            if stmt:
                _exec(stmt)
            buf = ""
    tail = _strip_comments(buf)
    if tail:
        _exec(tail)
    con.commit()
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);",
                         schema_sql, re.S):
        table, body = m.group(1), m.group(2)
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue
        for line in body.split("\n"):
            line = line.split("--")[0].strip().rstrip(",")
            if not line or line.upper().startswith(("PRIMARY", "UNIQUE", "FOREIGN", "CHECK")):
                continue
            parts = line.split()
            col, decl = parts[0], " ".join(parts[1:])
            if col in have or "PRIMARY KEY" in decl.upper() or "UNIQUE" in decl.upper():
                continue
            # ЧЕРЕЗ ТО ЖЕ ОЖИДАНИЕ, что и всё остальное. Раньше добавление
            # колонки шло напрямую, мимо повторов: CREATE умел ждать снятия
            # блокировки, а ALTER — нет. Достаточно было работающего воркера,
            # чтобы миграция упала с «database is locked», а вместе с ней —
            # любой процесс, которому эта колонка нужна.
            _exec(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    con.commit()
    _SCHEMA_DONE.add(key)
    return con



def write(con, sql, params=(), tries=6):
    """Запись, которая ждёт снятия блокировки и повторяет попытку.

    busy_timeout покрывает не всё: при занятой базе отдельные операции всё
    равно отказывают сразу. Каждый такой отказ раньше выглядел как поломка
    того, кто писал, — агент не мог завести себе строку только потому, что
    воркер в эту секунду крутил цикл. Ждать и повторить дешевле, чем
    разбираться потом, почему запись не появилась.
    """
    for i in range(tries):
        try:
            con.execute(sql, params)
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or i == tries - 1:
                raise
            time.sleep(1 + i)

def init():
    con = connect()
    ensure_schema(con, SCHEMA)
    return con

if __name__ == "__main__":
    con = init()
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"brain.db ready at {DB_PATH}")
    print(f"{len(tables)} tables: {', '.join(tables)}")

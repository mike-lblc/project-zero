"""Model router. Enforces DECISION_PROTOCOL.md section 1:
THE LOCAL MODEL MAY NEVER DECIDE. Mechanical work runs locally; judgment escalates."""
import json, os, urllib.request, urllib.error

OLLAMA = "http://127.0.0.1:11434/api/generate"

# МАРШРУТИЗАЦИЯ ПО ВЕСУ ЗАДАЧИ, а не одна модель на всё.
#
# На машине стоят три модели, и до этой правки использовалась одна — средняя.
# Это расточительно в обе стороны: разметка и классификация не нуждаются в
# шести гигабайтах, а разбор кода и рассуждение агента о собственном состоянии
# заметно выигрывают от тридцати.
#
# Тяжёлая модель грузится в память дольше, поэтому она берётся только там, где
# качество ответа действительно решает. Для остального — лёгкая: она отвечает
# быстрее и не выталкивает соседей из видеопамяти.
MODELS = {
    "light": "qwen3.5:4b",        # 3.4 ГБ — разметка, классификация, форматирование
    "standard": "qwen3.5:9b",     # 6.6 ГБ — извлечение фактов, перевод, сводки
    "heavy": "qwen3-coder:30b",   # 18.6 ГБ — разбор кода и рассуждение агента
}
LOCAL_MODEL = MODELS["standard"]          # значение по умолчанию

# Какой задаче какая модель.
#
# ЗАМЕР ОПРОВЕРГ ОЧЕВИДНОЕ ПРЕДПОЛОЖЕНИЕ. Казалось, что тяжёлая модель медленнее
# лёгкой. На деле три вызова к 30-миллиардной заняли 24 секунды, а к
# 9-миллиардной — 177: потому что тяжёлая уже лежала в видеопамяти, а лёгкая
# выталкивала её и грузилась заново.
#
# Значит враг здесь не размер модели, а ПЕРЕТАСОВКА: каждое переключение между
# моделями стоит выгрузки и загрузки гигабайтов. Поэтому горячий путь — всё,
# что вызывается часто, — идёт на ОДНОЙ модели, и это тяжёлая: раз уж она
# всё равно резидентна, пусть работает лучшая.
#
# Лёгкая остаётся только там, где вызовы редки и качество не решает.
WEIGHT = {
    "classify": "heavy",      # рассуждение агента — самый частый вызов
    "extract": "heavy",
    "parse": "heavy",
    "summarize": "heavy",
    "translate": "heavy",
    "tag": "light", "format": "light", "dedupe": "light",
}


# ГДЕ ЧТО ИСПОЛНЯЕТСЯ — ТРИ ЯВНЫХ РЕЖИМА, А НЕ УГАДЫВАНИЕ.
#
# Облачная модель — три миллиарда параметров против тридцати у домашней. Это
# не «чуть хуже», это другой класс: разметить и переформатировать она может,
# а рассуждение агента о собственном состоянии ей не по силам. Отдавать ей
# выбор действия значит получать правдоподобные, но плохие решения — и не
# отличать их от хороших, потому что в журнале они выглядят одинаково.
#
# Первая попытка решала это пробой: «спросим localhost, жив ли он». Проба была
# ошибкой сразу по двум причинам. В облаке localhost'а нет вовсе, и обращение
# туда — лишний запрос, который ничего не может вернуть. А главное, это
# нарушало правило, ради которого облачный путь и строился: при облачном
# режиме локальная модель не трогается, иначе тихий откат на неё выдаёт
# «рассуждение состоялось» там, где его не было.
#
# Поэтому режим ЗАДАЁТСЯ, а не угадывается:
#
#   ollama      (по умолчанию)  всё дома — обычная работа на машине владельца
#   cloudflare                  всё в облаке — там домашней модели физически
#                               нет, и тяжёлые задачи честно помечаются как
#                               исполненные слабой моделью
#   split                       рутина в облако, мышление дома — режим для
#                               машины владельца, когда облако разгружает
#
# Одна развилка, одно место, никаких вторых копий этого решения.
CLOUD_OK = {"tag", "format", "dedupe"}     # рутина: облачной модели по силам


def where(task_type):
    """Где исполнится задача и почему именно там. Объяснимо — значит проверяемо."""
    t = task_type.lower().strip()
    backend = os.environ.get("P0_MODEL_BACKEND", "ollama")
    local = MODELS.get(WEIGHT.get(t, "standard"), MODELS["standard"])

    if backend == "ollama":
        return "дома", local, "домашний режим: всё считает машина владельца"

    if backend == "split":
        if t in CLOUD_OK:
            from core.cloud_model import MODEL
            return "облако", MODEL, "рутина, качества облачной модели хватает"
        return "дома", local, "мышление остаётся дома: домашняя модель крупнее в десять раз"

    if backend == "cloudflare":
        from core.cloud_model import MODEL
        why = ("рутина, качества хватает" if t in CLOUD_OK else
               "ВЫНУЖДЕННО: домашней модели здесь нет, рассуждение идёт на слабой — "
               "решения будут хуже обычного")
        return "облако", MODEL, why

    raise RuntimeError(f"Unknown model backend: {backend}; refusing local fallback")


def model_for(task_type):
    """Какая модель возьмёт эту задачу. Выбор объясним и проверяем."""
    return where(task_type)[1]

MECHANICAL = {"extract","classify","tag","format","parse","dedupe","summarize","translate"}
JUDGMENT   = {"decide","choose","plan","approve","rule","propose","evaluate","prioritize","pivot"}

class EscalationRequired(Exception):
    """Raised when a JUDGMENT task is sent to the local model. Never suppress."""

def _local(prompt, timeout=300, model=None):
    """Запрос к локальной модели.

    Долгий таймаут не про медлительность модели, а про загрузку весов: если
    модель вытеснили из памяти, первый вызов ждёт возвращения восемнадцати
    гигабайт с диска. Короткий таймаут превращал это в «модель недоступна».
    """
    # РЕШЕНИЕ О МЕСТЕ ПРИНИМАЕТСЯ ОДИН РАЗ — в where(). Здесь его копии быть не
    # должно: две независимые развилки по одному признаку расходятся со
    # временем, и тогда журнал говорит одно, а исполнилось другое.
    backend = os.environ.get("P0_MODEL_BACKEND", "ollama")
    if backend not in ("ollama", "cloudflare"):
        raise RuntimeError("Unknown model backend; refusing local fallback")
    req = urllib.request.Request(OLLAMA,
        data=json.dumps({"model": model or LOCAL_MODEL, "prompt": prompt,
                         "stream": False}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("response", "")

def run(task_type, prompt, _retry=True):
    """task_type must be in MECHANICAL. Anything else escalates by design."""
    t = task_type.lower().strip()
    if t in JUDGMENT or t not in MECHANICAL:
        raise EscalationRequired(
            f"'{task_type}' is a JUDGMENT task. The local model is forbidden to decide. "
            f"Escalate to a Claude Code subagent.")
    place, m, why = where(t)
    if place == "облако":
        from core.cloud_model import generate
        from core import telemetry
        with telemetry.span("model", m):
            return generate(prompt, structured=(t == "classify"))
    # Время ответа модели — не любопытство, а вход в решение: именно замером
    # выяснилось, что тяжёлая модель отвечает БЫСТРЕЕ лёгкой, потому что уже
    # лежит в видеопамяти, а лёгкая выталкивает её и грузится заново.
    from core import telemetry
    with telemetry.span("model", m):
        out = _local(prompt, model=m)
    # Known failure mode: small local models return empty. Retry once, then escalate.
    if not out or not out.strip():
        if _retry:
            out = _local(prompt, model=m)
        if not out or not out.strip():
            raise EscalationRequired(
                f"Local model returned empty twice for '{task_type}'. Escalating.")
    return out

def health():
    try:
        # Cold-start VRAM load can take minutes if another model must be evicted.
        return True, _local("Reply with the single word: ready", timeout=420).strip()[:60]
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

"""Model router. Enforces DECISION_PROTOCOL.md section 1:
THE LOCAL MODEL MAY NEVER DECIDE. Mechanical work runs locally; judgment escalates."""
import json, urllib.request, urllib.error

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


def model_for(task_type):
    """Какая модель возьмёт эту задачу. Выбор объясним и проверяем."""
    return MODELS.get(WEIGHT.get(task_type.lower().strip(), "standard"),
                      MODELS["standard"])

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
    m = model_for(t)
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

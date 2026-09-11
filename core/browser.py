"""БРАУЗЕР КАК ИНСТРУМЕНТ АГЕНТА — с жёсткими границами.

Зачем понадобился. Половина площадок рисует содержимое скриптом, и обычный
запрос отдаёт пустую страницу. Замер это показал прямо: у нескольких найденных
площадок «сумм на странице: 0» при том, что задачи там есть. Мы объявляли их
непригодными, хотя на деле просто не умели смотреть.

ПОЧЕМУ ЗДЕСЬ БОЛЬШЕ ЗАПРЕТОВ, ЧЕМ ВЕЗДЕ. Браузер — самая опасная возможность
из всех, что есть у агента. Он умеет не только читать, но и нажимать, вводить
и отправлять. Ошибка в чтении стоит пустого результата; ошибка в нажатии может
стоить аккаунта, денег или чужого доверия.

Поэтому здесь ТОЛЬКО ЧТЕНИЕ, и это не настройка, а устройство:

    открыть страницу и снять текст        можно
    снять структурированные данные        можно
    дождаться прорисовки скриптом         можно
    нажать, ввести, отправить форму       НЕЛЬЗЯ — метода просто нет
    войти в аккаунт                       НЕЛЬЗЯ — класс BLACK
    пройти проверку «я не робот»          НЕЛЬЗЯ — класс BLACK

Отдельно про адреса: агент не может открыть что угодно. Список разрешённых
площадок ведётся явно, и добавляется туда только то, что уже прошло проверку
разведчика о четыре стены. Браузер, открывающий произвольный адрес по решению
модели, — это готовый канал для внедрения инструкций со стороны.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import guard  # noqa: E402

# Куда агенту разрешено смотреть. Только площадки, уже проверенные разведчиком.
# Список растёт не по желанию модели, а по результату проверки о стены.
ALLOWED = (
    "mlcontests.com", "immunefi.com", "replit.com", "algora.io",
    "bounties.network", "solanacompass.com", "gitcoin.co", "opire.dev",
    "console.algora.io", "github.com", "registry.modelcontextprotocol.io",
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


class NotAllowed(Exception):
    """Адрес вне списка разрешённых площадок."""


def _host_ok(url):
    m = re.match(r"https?://([^/:?#]+)", url or "")
    if not m:
        return False
    host = m.group(1).lower().removeprefix("www.")
    return any(host == a or host.endswith("." + a) for a in ALLOWED)


def read(url, wait_for=None, timeout=30000):
    """Открывает страницу, дожидается прорисовки и отдаёт текст и ссылки.

    Возвращает словарь: заголовок, текст, ссылки, найденные суммы. Ничего не
    нажимает и не вводит — таких методов здесь нет по устройству, а не по
    договорённости.
    """
    guard.check_action("browse", "GREEN")
    if not _host_ok(url):
        raise NotAllowed(f"адрес вне списка разрешённых площадок: {url[:80]}")

    from playwright.sync_api import sync_playwright
    out = {"url": url, "title": "", "text": "", "links": [], "amounts": []}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            ctx = b.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
            pg = ctx.new_page()
            # Картинки и шрифты не нужны: они только тратят время и трафик.
            pg.route(re.compile(r"\.(png|jpe?g|gif|svg|webp|woff2?|ttf)$"),
                     lambda route: route.abort())
            pg.goto(url, timeout=timeout, wait_until="domcontentloaded")
            if wait_for:
                try:
                    pg.wait_for_selector(wait_for, timeout=12000)
                except Exception:
                    pass          # не дождались — отдаём что есть, а не падаем
            pg.wait_for_timeout(1500)
            out["title"] = pg.title()
            out["text"] = re.sub(r"\s+", " ", pg.inner_text("body"))[:60000]
            out["links"] = [
                {"text": (a.get("t") or "").strip()[:120], "href": a.get("h")}
                for a in pg.eval_on_selector_all(
                    "a[href]", "els => els.map(e => ({t: e.innerText, h: e.href}))")
                if a.get("h")][:300]
        finally:
            b.close()
    out["amounts"] = sorted(set(re.findall(r"\$\s?[\d,]{2,}", out["text"])))[:40]
    return out


def probe(url, wait_for=None):
    """Короткая сводка: видно ли на странице деньги и сколько ссылок.

    Нужна, чтобы отличить «площадка пустая» от «мы не умели её прочитать».
    Этой разницы нам не хватало: страницы, рисуемые скриптом, объявлялись
    непригодными по пустому ответу обычного запроса.
    """
    try:
        d = read(url, wait_for=wait_for)
    except NotAllowed as e:
        return {"ok": False, "why": str(e)}
    except Exception as e:
        return {"ok": False, "why": f"{type(e).__name__}: {str(e)[:120]}"}
    return {"ok": True, "title": d["title"][:90], "amounts": len(d["amounts"]),
            "examples": d["amounts"][:5], "links": len(d["links"]),
            "text_len": len(d["text"])}


if __name__ == "__main__":
    for u, sel in [("https://algora.io/bounties", None),
                   ("https://bounties.network/", None),
                   ("https://replit.com/bounties", None)]:
        r = probe(u, sel)
        print(f"{u}\n   {r}\n")

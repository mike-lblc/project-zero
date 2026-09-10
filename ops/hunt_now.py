"""Разовый заход охотника за баунти с чистой базой."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from agents import bounty

c = connect()
c.execute("DELETE FROM bounties")
c.commit()
c.close()

print("ИТОГ:", bounty.hunt(150))
print()
print("=== НЕЗАНЯТЫЕ ЗАДАЧИ С НАСТОЯЩИМИ ДЕНЬГАМИ ===")
rows = bounty.shortlist(14)
if not rows:
    print("  пусто — все найденные либо заняты, либо платят не деньгами")
for b in rows:
    print(f"  ${b['amount']:>7.0f}  звёзд {b['stars']:>5}  {b['language'][:11]:11} {b['repo'][:34]}")
    print(f"           {b['title'][:86]}")
    print(f"           {b['url']}")

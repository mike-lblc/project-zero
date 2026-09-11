"""Publish only aggregate cloud telemetry, never credentials, message bodies or leads."""
import json,os,sqlite3,urllib.request
from datetime import datetime,timezone
from pathlib import Path
ACCOUNT="a8dd50703f4d727cd15c934aed9582d4"
DATABASE="0c63cbd9-6f10-4e69-b48f-dcfc60432490"

def publish(step="waiting",agent="orchestrator",phase="waiting"):
    token=os.environ.get("P0_CLOUDFLARE_AI_TOKEN")
    if not token:raise RuntimeError("Live state credential unavailable")
    c=sqlite3.connect(str(Path(__file__).resolve().parent.parent/'data'/'brain.db'))
    c.row_factory=sqlite3.Row
    def query(sql):
        try:return [dict(r) for r in c.execute(sql)]
        except sqlite3.Error:return []
    def count(table):
        try:return c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
        except sqlite3.Error:return None
    rows=query("SELECT id,agent,started_at,ended_at,status FROM runs ORDER BY id DESC LIMIT 80")
    agents=query("SELECT agent AS id,COUNT(*) AS work,MAX(started_at) AS last FROM runs GROUP BY agent")
    for a in agents:a['role']=a['id']
    payments,spend=count('payments'),count('spend')
    c.close()
    stamp=datetime.now(timezone.utc).isoformat()
    state={'status':{'agents':agents,'payments':payments,'spend':spend,'mission':{'payments':payments,'spend':spend},'cloud_step':step,'cloud_agent':agent,'cloud_phase':phase},'pulse':{'runs':rows},'queue':{'restricted':True},'execution':{'restricted':True},'chat':{'restricted':True,'messages':[]}}
    payload={'sql':"INSERT INTO live_state(id,body,updated_at) VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at",'params':[json.dumps(state,ensure_ascii=False),stamp]}
    req=urllib.request.Request(f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/d1/database/{DATABASE}/query',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as response:data=json.load(response)
    if not data.get('success') or not all(r.get('success') for r in data.get('result',[])):raise RuntimeError('Cloud telemetry write failed')

"""Recurring bounded audit. Reports gaps; never fabricates settlement or arbitrary fixes."""
import ast,json,sqlite3,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def main():
    findings=[]
    for folder in ('core','agents','ops'):
        for p in (ROOT/folder).glob('*.py'):
            try:ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))
            except (SyntaxError,UnicodeError) as e:findings.append({'category':'code','file':str(p.relative_to(ROOT)),'error':type(e).__name__})
    run=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','evals','-p','test_*.py'],cwd=ROOT,capture_output=True,text=True,timeout=240)
    if run.returncode:findings.append({'category':'regression','error':'tests failed'})
    ledger={}; failures=[]
    path=ROOT/'data'/'brain.db'
    if path.exists():
        c=sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True)
        try:
            check=c.execute('PRAGMA integrity_check').fetchone()[0]
            if check!='ok':findings.append({'category':'storage','error':check})
            for table in ('payments','spend','subscribers'):
                try:ledger[table]=c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                except sqlite3.Error:ledger[table]=None
            try:
                rows=c.execute("SELECT notes FROM runs WHERE status IN ('error','failed') ORDER BY id DESC LIMIT 200").fetchall()
                patterns={'code':['NameError','AttributeError','ImportError'],'api':['403','401','429','timeout','HTTPError'],'storage':['locked','sqlite','schema'],'payments':['settle','payment','wallet'],'access':['captcha','KYC'],'sync':['event','queue','task']}
                for category,words in patterns.items():
                    failures.append({'category':category,'keyword_matches_not_diagnoses':sum(any(w.lower() in str(row[0]).lower() for w in words) for row in rows)})
            except sqlite3.Error:findings.append({'category':'observability','error':'run history unavailable'})
        finally:c.close()
    else:findings.append({'category':'storage','error':'mission database unavailable'})
    report={'at':datetime.now(timezone.utc).isoformat(),'tests_passed':run.returncode==0,'findings':findings,'recent_failure_patterns':failures,'ledger':ledger,'settlement_proven':False,'limits':['No real customer payment created by this test','No universal zero-bug or availability guarantee','Unknown code defects require a reviewed patch and regression tests; no blind source rewrites']}
    out=ROOT/'reports'/'deep-audit.json';out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    print(run.stderr[-4000:])
    return 1 if findings else 0
if __name__=='__main__':sys.exit(main())

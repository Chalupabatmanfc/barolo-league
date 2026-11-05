"""The Barolo League live Fantrax backend. Python 3.10+, standard library only."""
import csv, io, json, os, re, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

LEAGUE='9w76f1ygmqodmp3i'
ROOT=Path(__file__).parent
CACHE=ROOT/'data.json'
CSV=ROOT/'fallback.csv'
INTERVAL=max(300,int(os.getenv('SYNC_INTERVAL_SECONDS','1800')))
lock=threading.Lock()
state={'source':'none','updated_at':None,'standings':None,'matchups':{},'error':None}
transfers={'source':'pending','updated_at':None,'rows':[],'error':None}

def fetch(endpoint,period=None):
    url=f'https://www.fantrax.com/fxea/general/{endpoint}?leagueId={LEAGUE}'
    if period is not None:url+=f'&period={period}'
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; BaroloLeagueDashboard/1.0)','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=12) as response:
        result=json.load(response)
    return result

def fallback():
    if not CSV.exists():return []
    matches=[];week=None
    for row in csv.reader(io.StringIO(CSV.read_text(encoding='utf-8-sig'))):
        if not row:continue
        m=re.search(r'Gameweek\s+(\d+)',row[0],re.I)
        if m:week=int(m.group(1));continue
        if len(row)<4 or row[0].strip().lower()=='away' or week is None:continue
        try:a=float(row[1]);h=float(row[3])
        except ValueError:continue
        if a==0 and h==0:continue
        matches.append({'period':week,'away':row[0],'away_points':a,'home':row[2],'home_points':h})
    return matches

def normalize_games(payload):
    if isinstance(payload,list):return payload
    if isinstance(payload,dict):
        for key in ('matchups','games','data'):
            if isinstance(payload.get(key),list):return payload[key]
    return []

def sync():
    global state
    try:
        standings=fetch('getStandings')
        if not isinstance(standings,list) or len(standings)<2:raise ValueError('Standings response was not a team list')
        # The standings W-D-L field reveals the latest completed scoring period.
        completed=max((sum(int(v) for v in re.findall(r'\d+',str(team.get('points','')))) for team in standings),default=0)
        if completed<1:raise ValueError('Could not identify completed gameweeks')
        matchups={}
        for week in range(1,min(completed+1,60)):
            payload=fetch('getMatchupScores',week)
            games=normalize_games(payload)
            valid=[m for m in games if isinstance(m,dict) and isinstance(m.get('away'),dict) and isinstance(m.get('home'),dict) and m['away'].get('teamId') and m['home'].get('teamId') and m['away'].get('score') is not None and m['home'].get('score') is not None]
            if len(valid)!=len(standings)//2:raise ValueError(f'Gameweek {week} returned {len(valid)} of {len(standings)//2} fixtures')
            matchups[str(week)]=valid
        result={'source':'fantrax','updated_at':int(time.time()),'standings':standings,'matchups':matchups,'error':None}
    except Exception as exc:
        error=f'{type(exc).__name__}: {exc}'
        with lock:
            if state.get('source') in ('fantrax','csv'):
                state['error']=error
                return
        csv_matches=fallback()
        if not csv_matches:
            with lock:state['error']=error
            return
        result={'source':'csv','updated_at':int(time.time()),'standings':None,'matchups':{},'csv_matches':csv_matches,'error':error}
    with lock:
        state=result
        CACHE.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')

def sync_transfers():
    """Fetch Fantrax website transaction history; keep separate from standings sync."""
    url=f'https://www.fantrax.com/fxpa/req?leagueId={LEAGUE}'
    payload={'msgs':[{'method':'getTransactionDetailsHistory','data':{'leagueId':LEAGUE,'maxResultsPerPage':'100','executedOnly':True}}]}
    req=urllib.request.Request(url,data=json.dumps(payload).encode('utf-8'),headers={
        'User-Agent':'Mozilla/5.0 (compatible; BaroloLeagueDashboard/2.0)',
        'Accept':'application/json','Content-Type':'application/json','Origin':'https://www.fantrax.com',
        'Referer':f'https://www.fantrax.com/fantasy/league/{LEAGUE}/transactions'})
    try:
        with urllib.request.urlopen(req,timeout=18) as response:
            body=json.load(response)
        # Fantrax's website API wraps the result in responses[0].data.
        result=body
        if isinstance(body,dict) and isinstance(body.get('responses'),list) and body['responses']:
            result=body['responses'][0].get('data',body['responses'][0])
        if isinstance(result,dict) and isinstance(result.get('table'),dict):
            rows=result['table'].get('rows')
        elif isinstance(result,dict):
            rows=result.get('rows') or result.get('transactions')
        else:rows=result if isinstance(result,list) else None
        if not isinstance(rows,list):
            raise ValueError('Unexpected transaction response format (no rows array)')
        with lock:transfers.update(source='fantrax',updated_at=int(time.time()),rows=rows[:100],error=None)
    except Exception as exc:
        with lock:transfers['error']=f'{type(exc).__name__}: {exc}'

def transfer_worker():
    while True:
        sync_transfers()
        time.sleep(INTERVAL)

def worker():
    while True:
        sync()
        time.sleep(INTERVAL)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/' or path=='/index.html':
            body=(ROOT/'index.html').read_bytes();mime='text/html; charset=utf-8'
        elif path=='/api/transfers':
            with lock:data=dict(transfers)
            body=json.dumps(data,ensure_ascii=False).encode();mime='application/json; charset=utf-8'
        elif path in ('/api/data','/api/status'):
            with lock: data=dict(state)
            if path=='/api/status':data={k:data.get(k) for k in ('source','updated_at','error')}
            body=json.dumps(data,ensure_ascii=False).encode();mime='application/json; charset=utf-8'
        else:
            self.send_error(404);return
        self.send_response(200)
        self.send_header('Content-Type',mime)
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)

if __name__=='__main__':
    if CACHE.exists():
        try:state.update(json.loads(CACHE.read_text(encoding='utf-8')))
        except (OSError,ValueError):pass
    threading.Thread(target=worker,daemon=True).start()
    threading.Thread(target=transfer_worker,daemon=True).start()
    port=int(os.getenv('PORT','8000'))
    print(f'Barolo League listening on http://localhost:{port}',flush=True)
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()

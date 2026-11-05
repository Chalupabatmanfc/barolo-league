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
transfer_diagnostic={'checked_at':None,'http_status':None,'content_type':None,'response_type':None,'top_level_keys':[],'response_count':None,'data_keys':[],'table_keys':[],'rows_path':None,'row_count':None,'error':None}

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

# Administrator edits are authenticated on the server. Set ADMIN_TOKEN in Render.
import base64, hmac
from urllib.parse import unquote
ADMIN_TOKEN=os.getenv('ADMIN_TOKEN','')
STORE=Path(os.getenv('BAROLO_DATA_DIR',str(ROOT/'storage')))
STORE.mkdir(parents=True,exist_ok=True)
PROFILES=STORE/'profiles.json'
HISTORY=STORE/'history.json'
HISTORY_SEED=ROOT/'historical_seed.json'
def load_json(path,default):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return default
def save_json(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');tmp.replace(path)
def parse_history_csv(raw):
    # Supports the Fantrax matchup CSV layout: Gameweek N, Away, FPts, Home, FPts.
    week=None;out=[]
    for row in csv.reader(io.StringIO(raw.lstrip('\ufeff'))):
        if not row:continue
        m=re.search(r'Gameweek\s*(\d+)',row[0],re.I)
        if m:week=int(m.group(1));continue
        if len(row)<4 or week is None or row[0].strip().lower()=='away':continue
        try:a=float(row[1]);b=float(row[3])
        except (ValueError,TypeError):continue
        if not row[0].strip() or not row[2].strip() or a==0 and b==0:continue
        if not all(map(lambda x:x==x and abs(x)<100000,(a,b))):continue
        out.append({'week':week,'away':row[0].strip(),'home':row[2].strip(),'a':a,'h':b})
    return out

def worker():
    while True:
        sync()
        time.sleep(INTERVAL)

class Handler(BaseHTTPRequestHandler):
    def reply(self,status,data):
        body=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def authorized(self):
        provided=self.headers.get('X-Admin-Token','')
        return bool(ADMIN_TOKEN) and hmac.compare_digest(provided,ADMIN_TOKEN)
    def do_GET(self):
        path=urlparse(self.path).path
        if path in ('/','/index.html'):
            body=(ROOT/'index.html').read_bytes()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        if path=='/api/profiles':return self.reply(200,load_json(PROFILES,{}))
        if path=='/api/history':
            seed=load_json(HISTORY_SEED,{})
            seed.update(load_json(HISTORY,{}))
            return self.reply(200,seed)
        if path in ('/api/data','/api/status'):
            with lock:data=dict(state)
            if path=='/api/status':data={k:data.get(k) for k in ('source','updated_at','error')}
            return self.reply(200,data)
        self.send_error(404)
    def do_POST(self):
        path=urlparse(self.path).path
        if path not in ('/api/admin/profile','/api/admin/history'):return self.reply(404,{'error':'Not found'})
        if not self.authorized():return self.reply(403,{'error':'Admin token missing, incorrect or not configured on server'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=3000000:raise ValueError('Upload must be below 3 MB')
            data=json.loads(self.rfile.read(size))
            if path.endswith('/profile'):
                team=str(data.get('team','')).strip()[:150]
                if not team:raise ValueError('Team name required')
                bio=str(data.get('bio',''))[:3000]
                manager=str(data.get('manager',''))[:120]
                photo=data.get('photo','')
                if not isinstance(photo,str) or len(photo)>1500000 or (photo and not re.fullmatch(r'data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+',photo)):raise ValueError('Photo must be a PNG, JPEG or WebP under 1 MB')
                profiles=load_json(PROFILES,{})
                profiles[team]={'manager':manager,'bio':bio,'photo':photo}
                save_json(PROFILES,profiles)
                return self.reply(200,{'ok':True})
            season=str(data.get('season',''))
            if season not in [f'{y}-{y+1}' for y in range(2017,2026)]:raise ValueError('Invalid historical season')
            raw=data.get('csv','')
            if not isinstance(raw,str) or len(raw)>2500000:raise ValueError('CSV too large')
            rows=parse_history_csv(raw)
            if not rows:raise ValueError('No completed matchups found in CSV')
            history=load_json(HISTORY,{})
            history[season]=rows
            save_json(HISTORY,history)
            return self.reply(200,{'ok':True,'fixtures':len(rows)})
        except (ValueError,TypeError,UnicodeError) as exc:return self.reply(400,{'error':str(exc)})

if __name__=='__main__':
    if CACHE.exists():
        try:state.update(json.loads(CACHE.read_text(encoding='utf-8')))
        except (OSError,ValueError):pass
    threading.Thread(target=worker,daemon=True).start()
    port=int(os.getenv('PORT','8000'))
    print(f'Barolo League listening on http://localhost:{port}',flush=True)
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()

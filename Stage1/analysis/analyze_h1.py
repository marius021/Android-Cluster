#!/usr/bin/env python3
import argparse, csv, json, sqlite3
from datetime import datetime
from pathlib import Path

EXP='H1_S5_HONEYGAIN_72H'; SERIAL='52000a84b2b21305'; INTERVAL=30; GAP=120

def dt(s): return datetime.strptime(s,'%Y-%m-%d %H:%M:%S')
def mib(x): return x/1024**2
def gib(x): return x/1024**3

def main():
    p=argparse.ArgumentParser(); p.add_argument('--db',required=True); p.add_argument('--out',default='results'); p.add_argument('--honeygain'); a=p.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row; c=con.cursor(); wh='experiment_id=? AND serial=?'; par=(EXP,SERIAL)
    first=c.execute(f'SELECT MIN(timestamp) FROM measurements WHERE {wh}',par).fetchone()[0]
    last=c.execute(f'SELECT MAX(timestamp) FROM measurements WHERE {wh}',par).fetchone()[0]
    n=c.execute(f'SELECT COUNT(*) FROM measurements WHERE {wh}',par).fetchone()[0]
    if not first: raise SystemExit('No H1 measurements found')
    secs=(dt(last)-dt(first)).total_seconds(); expected=int(secs/INTERVAL)+1
    valid=c.execute(f'''SELECT COUNT(*) FROM measurements WHERE {wh} AND rx_interval_bytes IS NOT NULL AND tx_interval_bytes IS NOT NULL''',par).fetchone()[0]
    rx=c.execute(f'''SELECT COALESCE(SUM(rx_interval_bytes),0) FROM measurements WHERE {wh} AND rx_interval_bytes IS NOT NULL AND tx_interval_bytes IS NOT NULL''',par).fetchone()[0]
    tx=c.execute(f'''SELECT COALESCE(SUM(tx_interval_bytes),0) FROM measurements WHERE {wh} AND rx_interval_bytes IS NOT NULL AND tx_interval_bytes IS NOT NULL''',par).fetchone()[0]
    temp=c.execute(f'''SELECT MIN(temperature_c),AVG(temperature_c),MAX(temperature_c) FROM measurements WHERE {wh} AND temperature_c IS NOT NULL''',par).fetchone()
    rsrp=c.execute(f'''SELECT MIN(rsrp_dbm),AVG(rsrp_dbm),MAX(rsrp_dbm) FROM measurements WHERE {wh} AND rsrp_dbm IS NOT NULL''',par).fetchone()
    rsrq=c.execute(f'''SELECT MIN(rsrq_db),AVG(rsrq_db),MAX(rsrq_db) FROM measurements WHERE {wh} AND rsrq_db IS NOT NULL''',par).fetchone()
    gaps=c.execute(f'''WITH q AS (SELECT timestamp,LAG(timestamp) OVER(ORDER BY timestamp) prev FROM measurements WHERE {wh}) SELECT timestamp,prev,ROUND((julianday(timestamp)-julianday(prev))*86400,1) gap FROM q WHERE prev IS NOT NULL AND (julianday(timestamp)-julianday(prev))*86400>? ORDER BY timestamp''',par+(GAP,)).fetchall()
    events=c.execute('SELECT timestamp,event_type,serial,experiment_id,old_value,new_value,details FROM events WHERE experiment_id=? ORDER BY timestamp',(EXP,)).fetchall()
    errors=[dict(x) for x in events if x['event_type']=='COLLECTION_ERROR']
    disconnects=[dict(x) for x in events if x['event_type']=='ADB_DISCONNECTED']
    connects=[dict(x) for x in events if x['event_type']=='ADB_CONNECTED']
    hourly=c.execute(f'''SELECT substr(timestamp,1,13)||':00:00' hour,COUNT(*) samples,COALESCE(SUM(rx_interval_bytes),0) rx,COALESCE(SUM(tx_interval_bytes),0) tx,COALESCE(SUM(rx_interval_bytes+tx_interval_bytes),0) total FROM measurements WHERE {wh} AND rx_interval_bytes IS NOT NULL AND tx_interval_bytes IS NOT NULL GROUP BY substr(timestamp,1,13) ORDER BY hour''',par).fetchall()
    with open(out/'h1_hourly_traffic.csv','w',newline='') as f:
        w=csv.writer(f); w.writerow(['hour','samples','rx_mib','tx_mib','total_mib']); [w.writerow([r['hour'],r['samples'],f'{mib(r["rx"]):.2f}',f'{mib(r["tx"]):.2f}',f'{mib(r["total"]):.2f}']) for r in hourly]
    with open(out/'h1_events.csv','w',newline='') as f:
        w=csv.writer(f); w.writerow(['timestamp','event_type','serial','experiment_id','old_value','new_value','details']); [w.writerow([r['timestamp'],r['event_type'],r['serial'],r['experiment_id'],r['old_value'],r['new_value'],r['details']]) for r in events]
    hg=[]
    if a.honeygain:
        with open(a.honeygain,newline='') as f: hg=[{'date':r['date'],'credits':float(r['credits']),'usd':float(r['usd'])} for r in csv.DictReader(f)]
    sd=dt(first).date(); ed=dt(last).date(); full=[x for x in hg if sd < datetime.strptime(x['date'],'%Y-%m-%d').date() < ed]; overlap=[x for x in hg if sd <= datetime.strptime(x['date'],'%Y-%m-%d').date() <= ed]
    result={'experiment':EXP,'device':{'serial':SERIAL,'model':'Samsung S5 Neo'},'measurement_period':{'first':first,'last':last,'duration_hours':secs/3600,'duration_days':secs/86400,'samples':n,'expected_from_span':expected,'sample_ratio':n/expected},'data_quality':{'valid_traffic_measurements':valid,'traffic_missing':n-valid,'gaps_over_120s':len(gaps),'collection_errors':len(errors),'event_counts':{t:sum(1 for x in events if x['event_type']==t) for t in sorted(set(x['event_type'] for x in events))}},'traffic':{'rx_mib':mib(rx),'tx_mib':mib(tx),'total_mib':mib(rx+tx),'total_gib':gib(rx+tx),'mib_per_hour':mib(rx+tx)/(secs/3600),'gib_per_day':gib(rx+tx)/(secs/86400)},'temperature_c':{'min':temp[0],'avg':temp[1],'max':temp[2]},'rsrp_dbm':{'min':rsrp[0],'avg':rsrp[1],'max':rsrp[2]},'rsrq_db':{'min':rsrq[0],'avg':rsrq[1],'max':rsrq[2]},'gaps_over_120s':[dict(x) for x in gaps],'events':{'collection_errors':errors,'adb_disconnects':disconnects,'adb_connects':connects},'honeygain':{'note':'Daily values are not exact H1 revenue because H1 starts/ends part-way through calendar days.','daily_rows':hg,'full_calendar_days_inside_h1':full,'full_day_credits':sum(x['credits'] for x in full),'full_day_usd':sum(x['usd'] for x in full),'overlapping_daily_credits_not_h1_revenue':sum(x['credits'] for x in overlap),'overlapping_daily_usd_not_h1_revenue':sum(x['usd'] for x in overlap)}}
    (out/'h1_results.json').write_text(json.dumps(result,indent=2))
    lines=[f'# {EXP} results','',f'- Period: `{first}` → `{last}`',f'- Duration: **{secs/86400:.2f} days**',f'- Measurements: **{n}** (expected ~{expected})',f'- Valid traffic measurements: **{valid}**',f'- Collection errors: **{len(errors)}**',f'- Gaps >120 s: **{len(gaps)}**','', '## Traffic',f'- RX: **{mib(rx):.2f} MiB**',f'- TX: **{mib(tx):.2f} MiB**',f'- Total: **{mib(rx+tx):.2f} MiB ({gib(rx+tx):.4f} GiB)**',f'- Span-equivalent: **{gib(rx+tx)/(secs/86400):.4f} GiB/day**','', '## Device conditions',f'- Temperature: **{temp[0]:.1f} / {temp[1]:.2f} / {temp[2]:.1f} °C**',f'- RSRP: **{rsrp[0]:.1f} / {rsrp[1]:.2f} / {rsrp[2]:.1f} dBm**',f'- RSRQ: **{rsrq[0]:.1f} / {rsrq[1]:.2f} / {rsrq[2]:.1f} dB**','', '## Honeygain',f'- Full calendar days entirely inside H1: **{sum(x["credits"] for x in full):.2f} credits / ${sum(x["usd"] for x in full):.2f}**','- The four daily values overlapping H1 are not treated as exact H1 revenue.','']
    (out/'h1_report.md').write_text('\n'.join(lines))
    print(f'Report written to {out.resolve()}'); print(f'Traffic: {mib(rx+tx):.2f} MiB ({gib(rx+tx):.4f} GiB)'); print(f'Samples: {n} / expected ~{expected}'); print(f'Errors: {len(errors)}; gaps >120s: {len(gaps)}')

if __name__=='__main__': main()

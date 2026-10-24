#!/usr/bin/env python3
"""Attended ROOT readback and bounded rendezvous for the committed rollout proof."""
import argparse, datetime as dt, hashlib, json, re, subprocess
from pathlib import Path

P=argparse.ArgumentParser();P.add_argument('mode',choices=['ready','intent','promoted']);P.add_argument('--workflow',required=True);P.add_argument('--workflow-uid',required=True);P.add_argument('--operation',required=True);P.add_argument('--source',required=True);P.add_argument('--old-digest',required=True);P.add_argument('--new-digest',required=True);P.add_argument('--revision');P.add_argument('--output',required=True);a=P.parse_args()
assert re.fullmatch(r'[a-z0-9-]{3,63}',a.operation) and re.fullmatch(r'[0-9a-f]{40}',a.source)
assert all(re.fullmatch(r'sha256:[0-9a-f]{64}',x) for x in [a.old_digest,a.new_digest]) and a.old_digest!=a.new_digest
if a.mode!='ready': assert a.revision and re.fullmatch(r'[0-9a-f]{40}',a.revision)
out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
def run(args,input=None):
 return subprocess.check_output(['kubectl','--context','vallery','--request-timeout=15s',*args],input=input,timeout=45)
def get(kind,name,ns='agent-fleet-ci'):
 return json.loads(run(['-n',ns,'get',kind,name,'-o','json']))
def binding():
 w=get('workflow',a.workflow);assert w['metadata']['uid']==a.workflow_uid and w['status']['phase']=='Running'
 nodes=[n for n in w['status'].get('nodes',{}).values() if n.get('type')=='Pod' and any(x.get('name')=='name' and x.get('value')=='web-rollout-evidence' for x in n.get('inputs',{}).get('parameters',[])) and n.get('phase')=='Running'];assert len(nodes)==1
 pods=json.loads(run(['-n','agent-fleet-ci','get','pods','-l','workflows.argoproj.io/workflow='+a.workflow,'-o','json']))['items'];pods=[p for p in pods if p['metadata'].get('annotations',{}).get('workflows.argoproj.io/node-id')==nodes[0]['id']];assert len(pods)==1
 p=pods[0];assert any(o.get('uid')==a.workflow_uid for o in p['metadata'].get('ownerReferences',[]));assert any(c['name']=='main' and 'running' in c.get('state',{}) for c in p['status'].get('containerStatuses',[]))
 return p
p=binding();pod=p['metadata']['name'];uid=p['metadata']['uid']
def exe(js,*args,input=None):
 live=binding();assert live['metadata']['uid']==uid
 return run(['-n','agent-fleet-ci','exec','-i',pod,'-c','main','--','node','-e',js,*args],input)
raw=exe("const f=require('node:fs');const p='/workspace/weather-web-rollout/ready.json';const b=f.readFileSync(p);if(b.length>60000)throw Error('ready budget');process.stdout.write(b)")
r=json.loads(raw);assert r['kind']=='weather-web-rollout-ready-v1' and r['operation_id']==a.operation and r['source_revision']==a.source and r['old_image_digest']==a.old_digest and r['expected_image_digest']==a.new_digest
assert dt.datetime.fromisoformat(r['expires_at'].replace('Z','+00:00'))>dt.datetime.now(dt.timezone.utc)
assert {x['name'] for x in r['engines']}=={'chromium','webkit','firefox'} and len(r['engines'])==3
assets=r['assets'];assert 0<len(assets)<=64;seen=set()
for x in assets:
 path=x['path'];assert re.fullmatch(r'/assets/[A-Za-z0-9_./-]+',path) and not any(v in ['','.','..'] for v in path.split('/')[1:]);assert path not in seen;seen.add(path)
 assert x['url']=='https://weather.vallery.net'+path and re.fullmatch(r'[0-9a-f]{64}',x['sha256']) and isinstance(x['bytes'],int) and 0<=x['bytes']<=8388608
 assert all(v in x['cache_control'] for v in ['public','immutable','max-age=31536000'])
assert sum(x['bytes'] for x in assets)<=67108864
ready_file=out/'ready.json'
if ready_file.exists():assert ready_file.read_bytes()==raw
else:ready_file.write_bytes(raw)
receipt={'operation_id':a.operation,'qualification_source':a.source,'workflow':a.workflow,'workflow_uid':a.workflow_uid,'pod':pod,'pod_uid':uid,'ready_sha256':hashlib.sha256(raw).hexdigest(),'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'old_image_digest':a.old_digest,'expected_image_digest':a.new_digest}
if a.mode=='ready':
 d=get('deployment','web','weather');assert d['spec']['replicas']==2 and d['status'].get('readyReplicas')==2
 assert d['spec']['template']['spec']['containers'][0]['image'].endswith('@'+a.old_digest)
 old=json.loads(run(['-n','weather','get','pods','-l','app.kubernetes.io/name=web,app.kubernetes.io/part-of=weather','-o','json']))['items'];old=[x for x in old if not x['metadata'].get('deletionTimestamp')];assert len(old)==2
 for item in old:
  c=[x for x in item['status'].get('containerStatuses',[]) if x['name']=='web'];assert len(c)==1 and c[0]['ready'] is True and c[0]['imageID'].endswith('@'+a.old_digest)
 receipt['old_web_pod_uids']=[x['metadata']['uid'] for x in old]
 receipt['state']='READY';(out/'operator-ready.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt));raise SystemExit
payload={'phase':a.mode,'operation_id':a.operation,'source_revision':a.source,'expected_image_digest':a.new_digest,'intended_revision':a.revision}
if a.mode=='promoted':
 ar=get('application','weather','argocd');assert ar['status']['sync']['status']=='Synced' and ar['status']['health']['status']=='Healthy' and ar['status']['sync']['revision']==a.revision
 d=get('deployment','web','weather');assert d['status'].get('observedGeneration')==d['metadata']['generation'] and d['spec']['replicas']==2 and d['status'].get('updatedReplicas')==d['status'].get('readyReplicas')==d['status'].get('availableReplicas')==2
 rows=json.loads(run(['-n','weather','get','pods','-l','app.kubernetes.io/name=web,app.kubernetes.io/part-of=weather','-o','json']))['items'];rows=[x for x in rows if not x['metadata'].get('deletionTimestamp')];assert len(rows)==2
 proof=[];origin=[]
 js="""const f=require('node:fs'),c=require('node:crypto');const rows=JSON.parse(process.argv[1]);for(const row of rows){const parts=row.path.split('/').slice(1);let p='/app/build/client';for(const x of parts){p+='/'+x;if(f.lstatSync(p).isSymbolicLink())throw Error('symlink')}const stat=f.statSync(p);if(!stat.isFile()||stat.size!==row.bytes||stat.size>8388608)throw Error('asset size');const hash=c.createHash('sha256').update(f.readFileSync(p)).digest('hex');if(hash!==row.sha256)throw Error('asset hash');}process.stdout.write(JSON.stringify(rows));"""
 for row in rows:
  statuses=[c for c in row['status'].get('containerStatuses',[]) if c['name']=='web'];assert len(statuses)==1 and statuses[0]['ready'] is True and 'running' in statuses[0].get('state',{}) and statuses[0]['imageID'].endswith('@'+a.new_digest)
  assert any(c['name']=='web' and c['image'].endswith('@'+a.new_digest) for c in row['spec']['containers'])
  name=row['metadata']['name'];poduid=row['metadata']['uid'];small=[{k:x[k] for k in ['path','bytes','sha256']} for x in assets]
  result=json.loads(run(['-n','weather','exec',name,'-c','web','--','node','-e',js,json.dumps(small,separators=(',',':'))]));assert result==small
  current=get('pod',name,'weather');assert current['metadata']['uid']==poduid
  proof.append({'uid':poduid,'name':name,'ready':True,'image_digest':a.new_digest});origin.extend([{**x,'pod_uid':poduid} for x in result])
 payload.update({'synced_revision':a.revision,'argo_sync':'Synced','argo_health':'Healthy','running_image_digest':a.new_digest,'web_pods':proof,'origin_assets':origin})
serialized=(json.dumps(payload,separators=(',',':'))+'\n').encode();assert len(serialized)<=60000
exe("const f=require('node:fs');const b=f.readFileSync(0);if(b.length>60000)throw Error('budget');const p='/workspace/weather-web-rollout/promotion-intent.json';f.writeFileSync(p+'.tmp',b);f.renameSync(p+'.tmp',p)",input=serialized)
receipt.update(payload);(out/('operator-'+a.mode+'.json')).write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'state':a.mode,'workflow_uid':a.workflow_uid,'pod_uid':uid,'intended_revision':a.revision,'origin_assets':len(payload.get('origin_assets',[]))}))

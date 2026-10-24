import datetime,json,pathlib,subprocess,time,sys
head='33501b424c63d38b01504e6d653c430952c00b50';name='repo-validate-jvallery-weather-'+head[:12]+'-pr';out=pathlib.Path('/Users/jason/maintenance/weather-cnpg-6/promotion71-33501b42-expected-custody.json')
k=['kubectl','--context','vallery','-n','agent-fleet-ci']
def get(args):
 r=subprocess.run(k+args,capture_output=True,text=True,check=True)
 return json.loads(r.stdout) if r.stdout.strip() else None
for _ in range(5):
 w=get(['get','workflow',name,'--ignore-not-found','-o','json'])
 if w:
  nodes=[n for n in w.get('status',{}).get('nodes',{}).values() if n.get('type')=='Pod' and n.get('displayName','').startswith('evidence(')]
  pods=get(['get','pods','-l','workflows.argoproj.io/workflow='+name,'-o','json'])['items']
  for n in nodes:
   matches=[p for p in pods if p['metadata']['name'].endswith('-'+n['id'].rsplit('-',1)[-1])]
   for p in matches:
    d={'workflow_uid':w['metadata']['uid'],'pod_uid':p['metadata']['uid'],'revision':head,'workflow_name':name,'pod_name':p['metadata']['name'],'captured_at':datetime.datetime.now(datetime.UTC).isoformat(),'node_id':n['id']};old=json.loads(out.read_text()) if out.exists() else None
    if old:assert old['workflow_uid']==d['workflow_uid']
    out.write_text(json.dumps(d,indent=2)+'\n');out.chmod(0o600);print(json.dumps({'captured':True,'workflow_uid':d['workflow_uid'],'pod_uid':d['pod_uid'],'pod_name':d['pod_name'],'receipt':str(out)}));sys.exit(0)
 time.sleep(8)
print(json.dumps({'captured':False,'reason':'evidence Pod not yet visible','workflow':name}));sys.exit(2)

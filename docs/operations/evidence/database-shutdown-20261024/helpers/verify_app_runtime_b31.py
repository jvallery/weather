"""Read-only artifact/rollout acceptance. Never opens a database connection."""
import datetime,hashlib,json,pathlib,subprocess,sys,urllib.request,urllib.error
repo='/Users/jason/repos/weather-app-promote-6';merge='da8b451a388c5271a79e5a320c2c1678be35f800';source='dd99be06a936b6adda37a355a15e52b0da62c803';digest='sha256:b31d9d54d737d6cc1607618f4aa0ba99f8c6a4c8e256a5b931d825f8984342a1';image='registry.vallery.net/jvallery/weather-app@'+digest;web='registry.vallery.net/jvallery/weather-web@sha256:0761f4ebf5f5cf73a4d261446b296fa77c1a3fb7e755585306c218a6e70764bd';k=['kubectl','--context','vallery']
def command(args):
 r=subprocess.run(args,capture_output=True)
 if r.returncode:raise RuntimeError('Consuming subprocess failed')
 return r.stdout
def get(*args):return json.loads(command(k+list(args)))
def main():
 app=get('-n','argocd','get','application','weather','-o','json');adopted=app['status']['sync']['revision'];assert app['status']['sync']['status']=='Synced' and app['status']['health']['status']=='Healthy';command(['git','-C',repo,'merge-base','--is-ancestor',merge,adopted])
 deps=get('-n','weather','get','deployment','api','web','-o','json')['items'];api=next(x for x in deps if x['metadata']['name']=='api');wd=next(x for x in deps if x['metadata']['name']=='web');assert api['spec']['replicas']==2 and api['status']['updatedReplicas']==api['status']['readyReplicas']==api['status']['availableReplicas']==2;assert api['status']['observedGeneration']==api['metadata']['generation'];assert api['spec']['template']['spec']['containers'][0]['image']==image;assert wd['spec']['template']['spec']['containers'][0]['image']==web and wd['status']['readyReplicas']==2
 files=['app/weather/db/schema_v1.py','app/weather/db/ddl_v1.py','app/migrations/versions/0000_namespace.py','app/migrations/versions/0001_schema.py','app/migrations/versions/0002_telemetry.py'];expected={f:hashlib.sha256(command(['git','-C',repo,'show',source+':'+f])).hexdigest() for f in files}
 pods=get('-n','weather','get','pods','-l','app.kubernetes.io/name=api','-o','json')['items'];pods=[p for p in pods if not p['metadata'].get('deletionTimestamp')];assert len(pods)==2;items=[]
 code='''import hashlib,json,os,pathlib,subprocess,urllib.request
from alembic.config import Config
from alembic.script import ScriptDirectory
files=FILES
hashes={f:hashlib.sha256(pathlib.Path('/app',f).read_bytes()).hexdigest() for f in files}
version=subprocess.check_output(['ffmpeg','-version'],text=True).splitlines()[0]
probe=subprocess.check_output(['ffprobe','-version'],text=True).splitlines()[0]
encoders=subprocess.check_output(['ffmpeg','-hide_banner','-encoders'],text=True,stderr=subprocess.DEVNULL)
with urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=5) as r:health=r.status
print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'files':hashes,'migrationHeads':ScriptDirectory.from_config(Config('/app/alembic.ini')).get_heads(),'ffmpeg':version,'ffprobe':probe,'libx264':'libx264' in encoders,'localHealth':health}))'''.replace('FILES',repr(files))
 for p in pods:
  cs=p['status']['containerStatuses'];assert len(cs)==1 and cs[0]['ready'] and cs[0]['restartCount']==0 and cs[0]['imageID'].endswith(digest);assert p['spec']['containers'][0]['image']==image
  runtime=json.loads(command(k+['-n','weather','exec',p['metadata']['name'],'-c',cs[0]['name'],'--','python','-c',code]));assert runtime['uid']==runtime['gid']==10001 and runtime['files']==expected and runtime['migrationHeads']==['0002_telemetry'] and runtime['libx264'] and runtime['localHealth']==200 and '9.0.2' in runtime['ffmpeg'] and '9.0.2' in runtime['ffprobe'];items.append({'pod':p['metadata']['name'],'uid':p['metadata']['uid'],'imageID':cs[0]['imageID'],'runtime':runtime})
 with urllib.request.urlopen(urllib.request.Request('https://weather.vallery.net/api/healthz',headers={'User-Agent':'weather-operator-verification/1'}),timeout=15) as response:public=response.status
 default_status=None;default_mitigated=None
 try:
  with urllib.request.urlopen('https://weather.vallery.net/api/healthz',timeout=15) as response:default_status=response.status
 except urllib.error.HTTPError as error:default_status=error.code;default_mitigated=error.headers.get('CF-Mitigated')
 assert public==200
 db=command(k+['-n','weather','get','cluster','weather-pg','--ignore-not-found','-o','name']);assert not db.strip()
 out={'verifiedAt':datetime.datetime.now(datetime.UTC).isoformat(),'promotionMerge':merge,'artifactSource':source,'image':image,'argo':{'revision':adopted,'sync':'Synced','health':'Healthy'},'apiPods':items,'webImagePreserved':web,'publicHealth':public,'publicProbe':{'path':'/api/healthz','operatorUserAgent':'weather-operator-verification/1','operatorStatus':public,'defaultPythonStatus':default_status,'defaultCloudflareMitigated':default_mitigated},'databaseExists':False,'databaseConnectionsOpenedByThisCheck':False};p=pathlib.Path('/Users/jason/maintenance/weather-cnpg-6/app-b31-live-verification.json');p.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({'verified':True,'receipt':str(p),'argoRevision':adopted,'apiPods':len(items),'migrationHead':'0002_telemetry','publicHealth':public,'publicProbe':{'path':'/api/healthz','operatorUserAgent':'weather-operator-verification/1','operatorStatus':public,'defaultPythonStatus':default_status,'defaultCloudflareMitigated':default_mitigated},'databaseExists':False}))
try:main()
except Exception as error:
 print(json.dumps({'verified':False,'errorClass':type(error).__name__,'details':'suppressed'}));sys.exit(1)

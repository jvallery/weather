"""Read-only post-merge equality proof; plaintext exists only in consuming process memory."""
import base64,datetime,hashlib,json,os,subprocess,sys
from pathlib import Path

repo='/Users/jason/repos/agents-weather-db-6'
receipt=Path('/Users/jason/maintenance/weather-cnpg-6/login-credential-source.json')
k=['kubectl','--context','vallery']

def command(args,**kwargs):
 r=subprocess.run(args,capture_output=True,**kwargs)
 if r.returncode:raise RuntimeError('Consuming subprocess failed; diagnostic suppressed')
 return r.stdout

def normalize(doc):
 result={name:base64.b64decode(value).decode() for name,value in doc.get('data',{}).items()}
 result.update(doc.get('stringData',{}));return result

def main():
 merge=sys.argv[1]
 if len(merge)!=40 or any(c not in '0123456789abcdef' for c in merge):
  raise ValueError('Expected exact accepted merge revision')
 app=json.loads(command(k+['-n','argocd','get','application','weather-secrets','-o','json']))
 sync=app['status']['sync'];health=app['status']['health']['status']
 assert sync['status']=='Synced' and health=='Healthy'
 adopted=sync['revision']
 command(['git','-C',repo,'merge-base','--is-ancestor',merge,adopted])
 env=os.environ.copy();env['SOPS_AGE_KEY_FILE']='/Users/jason/.config/sops/age/keys.txt'
 items=[]
 for item in json.loads(receipt.read_text()):
  path='platform/gitops/secrets-ksops/weather/secret-'+item['name']+'.enc.yaml'
  cipher=command(['git','-C',repo,'show',adopted+':'+path])
  assert hashlib.sha256(cipher).hexdigest()==item['ciphertext_sha256']
  plain=command(['sops','--decrypt','--input-type','yaml','--output-type','json','/dev/stdin'],input=cipher,env=env)
  source=json.loads(plain);expected=normalize(source)
  live=json.loads(command(k+['-n','weather','get','secret',item['name'],'-o','json']))
  assert live['type']==source['type']=='kubernetes.io/basic-auth'
  assert set(expected)==set(normalize(live))=={'username','password'}
  assert normalize(live)==expected
  assert expected['username']=='weather_'+item['name'].rsplit('-',1)[1]
  items.append({'name':item['name'],'uid':live['metadata']['uid'],'type':live['type'],
    'keys':sorted(expected),'ciphertextSha256':item['ciphertext_sha256'],
    'independentRecoveryEqualsLive':True})
 result={'verifiedAt':datetime.datetime.now(datetime.UTC).isoformat(),'acceptedMerge':merge,
  'argo':{'application':'weather-secrets','sync':'Synced','health':'Healthy','revision':adopted},
  'secrets':items,'databaseRoleLoginsProven':False,'databaseCommissionedByThisCheck':False}
 Path('/Users/jason/maintenance/weather-cnpg-6/login-live-custody.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result,indent=2))

try:main()
except Exception as error:
 print(json.dumps({'verified':False,'errorClass':type(error).__name__,'details':'suppressed'}));raise SystemExit(1)

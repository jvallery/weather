"""Exact independent source/live custody; plaintext stays in consuming-process memory."""
import base64,datetime,hashlib,json,os,pathlib,subprocess,sys
repo='/Users/jason/repos/agents-weather-db-6';merge='949da543149a68d9e830aa3eee3e33207176a45b';k=['kubectl','--context','vallery']
def call(args,**kwargs):
 r=subprocess.run(args,capture_output=True,**kwargs)
 if r.returncode:raise RuntimeError('Consuming subprocess failed')
 return r.stdout
def values(doc):
 result={name:base64.b64decode(value,validate=True) for name,value in doc.get('data',{}).items()}
 result.update({name:str(value).encode() for name,value in doc.get('stringData',{}).items()});return result
def main():
 app=json.loads(call(k+['-n','argocd','get','application','weather-secrets','-o','json']));s=app['status']['sync'];health=app['status']['health']['status'];assert s['status']=='Synced' and health=='Healthy';adopted=s['revision'];call(['git','-C',repo,'merge-base','--is-ancestor',merge,adopted])
 baseline=json.loads(pathlib.Path('/Users/jason/maintenance/weather-cnpg-6/login-rebase-a506-main.json').read_text())['cipherFiles'];assert len(baseline)==11;env=os.environ.copy();env['SOPS_AGE_KEY_FILE']='/Users/jason/.config/sops/age/keys.txt';rows=[]
 for row in baseline:
  cipher=call(['git','-C',repo,'show',adopted+':'+row['path']]);assert hashlib.sha256(cipher).hexdigest()==row['sha256'];plain=call(['sops','--decrypt','--input-type','yaml','--output-type','json','/dev/stdin'],input=cipher,env=env);source=json.loads(plain);kind=source['kind'];name=source['metadata']['name'];assert source['metadata']['namespace']=='weather' and kind in ('Secret','NetworkPolicy');live=json.loads(call(k+['-n','weather','get',kind,name,'-o','json']));item={'kind':kind,'name':name,'uid':live['metadata']['uid'],'namespace':'weather','sourceCiphertextSHA256':row['sha256'],'sourceBytesUnchanged':True}
  if kind=='Secret':
   assert source.get('type','Opaque')==live.get('type','Opaque');expected=values(source);assert expected==values(live);item.update(type=live.get('type','Opaque'),keys=sorted(expected),exactIndependentDecryptionEqualsLive=True)
   if name in {'weather-pg-'+r for r in ('ingest','worker','api','editor','migrator')}:
    assert set(expected)=={'username','password'} and expected['username']==('weather_'+name.rsplit('-',1)[-1]).encode()
  else:
   assert live['spec']==source['spec'];item['exactIndependentDecryptionSpecEqualsLive']=True
  rows.append(item)
 assert sum(r['kind']=='Secret' for r in rows)==10 and sum(r['kind']=='NetworkPolicy' for r in rows)==1
 out={'verifiedAt':datetime.datetime.now(datetime.UTC).isoformat(),'acceptedMerge':merge,'argo':{'application':'weather-secrets','revision':adopted,'sync':'Synced','health':'Healthy'},'objects':rows,'verifiedObjects':11,'verifiedSecrets':10,'verifiedPolicies':1,'databaseRoleLoginsProven':False,'databaseCreatedByThisCheck':False,'nadCreatedByThisCheck':False};p=pathlib.Path('/Users/jason/maintenance/weather-cnpg-6/all-weather-live-custody-949.json');p.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({'verified':True,'acceptedMerge':merge,'adoptedRevision':adopted,'objects':11,'secrets':10,'policies':1,'metadataReceipt':str(p)}))
try:main()
except Exception as error:
 print(json.dumps({'verified':False,'errorClass':type(error).__name__,'details':'suppressed'}));sys.exit(1)

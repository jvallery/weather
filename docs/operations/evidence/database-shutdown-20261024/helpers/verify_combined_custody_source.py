"""Independent metadata-only source preservation proof for the checked combined PR."""
import datetime,hashlib,json,pathlib,subprocess,sys,yaml
repo='/Users/jason/repos/agents-weather-db-6'
original='cea9d3ee1b188baf66efe16072964fddc5b66ac4'
base='a5061ad74b7adba8e57ed6aeb6ed8903c015c5aa'
root='platform/gitops/secrets-ksops/weather/'
policy='control-plane/agent-fleet-control/registry/policy/estate-ownership/'
def source(ref,path):
 r=subprocess.run(['git','-C',repo,'show',ref+':'+path],capture_output=True)
 if r.returncode:raise RuntimeError('Source lookup failed')
 return r.stdout
def rows(ref,path):return {r['id']:r for r in yaml.safe_load(source(ref,path))['entries']}
def main():
 ref=sys.argv[1]
 assert len(ref)==40 and all(c in '0123456789abcdef' for c in ref)
 baseline=json.loads(pathlib.Path('/Users/jason/maintenance/weather-cnpg-6/login-rebase-a506-main.json').read_text())
 ciphers=[]
 for row in baseline['cipherFiles']:
  actual=source(ref,row['path']);assert hashlib.sha256(actual).hexdigest()==row['sha256'];assert actual==source(original,row['path'])
  ciphers.append({'path':row['path'],'sha256':row['sha256'],'unchanged':True})
 assert len(ciphers)==11
 generator=root+'secret-generator.yaml';assert source(ref,generator)==source(original,generator)
 ex=rows(original,policy+'external.yaml');combined=rows(ref,policy+'external.yaml');ids=['credential:postgresql/weather-'+r for r in ('ingest','worker','api','editor','migrator')]
 for ident in ids:assert combined[ident]==ex[ident]
 contribution='contribution:weather/secret/jvallery/agents';old=rows(original,policy+'kubernetes-contributions.yaml')[contribution];current=rows(ref,policy+'kubernetes-contributions.yaml')[contribution];assert old==current
 # Every accepted external credential record remains intact, regardless of the new CNI records.
 before=rows(base,policy+'external.yaml')
 for ident,row in before.items():
  if ident.startswith('credential:'):assert combined[ident]==row
 paragraph='Central Weather PostgreSQL role login custody'
 owner=source(ref,'OWNERSHIP.md').decode();ownpatch=subprocess.run(['git','-C',repo,'diff',original+'^',original,'--','OWNERSHIP.md'],capture_output=True,check=True,text=True).stdout
 added=[line[1:] for line in ownpatch.splitlines() if line.startswith('+') and not line.startswith('+++') and line[1:].strip()]
 assert all(line in owner for line in added)
 result={'checkedAt':datetime.datetime.now(datetime.UTC).isoformat(),'combinedHead':ref,'originalCredentialCommit':original,'cipherFiles':ciphers,'generatorBytesUnchanged':True,'fiveRawCredentialRecordsIdentical':True,'weatherSecretContributionIdentical':True,'acceptedExternalCredentialRecordsPreserved':True,'dbOwnershipParagraphPreserved':True,'databaseActivatedByThisCheck':False}
 p=pathlib.Path('/Users/jason/maintenance/weather-cnpg-6/combined-custody-source-verification-'+ref[:12]+'.json');p.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'verified':True,'head':ref,'cipherFiles':11,'rawDBCredentialRecords':5,'generatorUnchanged':True,'secretContributionIdentical':True,'receipt':str(p)}))
try:main()
except Exception as e:
 print(json.dumps({'verified':False,'errorClass':type(e).__name__,'details':'suppressed'}));sys.exit(1)

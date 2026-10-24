"""Bounded manifest HEAD authorization with existing credentials only; no blobs/logged auth."""
import base64,datetime,json,pathlib,re,subprocess,sys,urllib.request,urllib.error,urllib.parse
k=['kubectl','--context','vallery']
def get(*a):return json.loads(subprocess.check_output(k+list(a),stderr=subprocess.DEVNULL))
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
accept='application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json'
def head(image,authorization=None):
 base,digest=image.split('@',1);host,repo=base.split('/',1);repo=repo.rsplit(':',1)[0] if ':' in repo else repo;url='https://'+host+'/v2/'+repo+'/manifests/'+digest;headers={'Accept':accept}
 if authorization:headers['Authorization']=authorization
 try:
  with opener.open(urllib.request.Request(url,method='HEAD',headers=headers),timeout=12) as r:status=r.status;found=r.headers.get('Docker-Content-Digest');media=r.headers.get('Content-Type')
 except urllib.error.HTTPError as e:
  if e.code!=401 or authorization or host!='ghcr.io':raise
  challenge=e.headers.get('WWW-Authenticate','');parts=dict(re.findall(r'(\w+)="([^"]+)"',challenge));realm=urllib.parse.urlsplit(parts.get('realm',''));assert realm.scheme=='https' and realm.netloc=='ghcr.io' and realm.path=='/token';assert parts.get('scope')=='repository:'+repo+':pull';query=urllib.parse.urlencode({'service':parts['service'],'scope':parts['scope']})
  with opener.open(urllib.request.Request(urllib.parse.urlunsplit((realm.scheme,realm.netloc,realm.path,query,''))),timeout=12) as r:raw=r.read(131073)
  assert len(raw)<=131072;token=json.loads(raw).get('token') or json.loads(raw).get('access_token');assert token;headers['Authorization']='Bearer '+token
  with opener.open(urllib.request.Request(url,method='HEAD',headers=headers),timeout=12) as r:status=r.status;found=r.headers.get('Docker-Content-Digest');media=r.headers.get('Content-Type')
 assert status==200 and found==digest
 return {'image':image,'method':'HEAD','status':status,'returnedDigest':found,'mediaType':media,'blobBytesDownloaded':0}
def main():
 secret=get('-n','weather','get','secret','zot-origin-cluster-pull','-o','json');assert secret['type']=='kubernetes.io/dockerconfigjson';config=json.loads(base64.b64decode(secret['data']['.dockerconfigjson']));entries=[v for key,v in config['auths'].items() if urllib.parse.urlsplit(key if '://' in key else 'https://'+key).hostname=='registry.vallery.net'];assert len(entries)==1;entry=entries[0];auth=entry.get('auth') or base64.b64encode((entry['username']+':'+entry['password']).encode()).decode();assert ':' in base64.b64decode(auth).decode();pg='registry.vallery.net/jvallery/mirror-ghcr-io-cloudnative-pg-postgresql-777ad6774e00fff6:16.15@sha256:d66df406385b9f3c43c47e94fe3a0002278ef56d21b82a8c9d3a22f91aa9c3f0';pgProof=head(pg,'Basic '+auth)
 deployment=get('-n','cnpg-system','get','deployment','barman-cloud','-o','json');env=next(e for c in deployment['spec']['template']['spec']['containers'] for e in c.get('env',[]) if e['name']=='SIDECAR_IMAGE');ref=env['valueFrom']['secretKeyRef'];sidecar=base64.b64decode(get('-n','cnpg-system','get','secret',ref['name'],'-o','json')['data'][ref['key']]).decode();source=subprocess.check_output(['git','-C','/Users/jason/repos/agents-weather-db-6','show','949da543149a68d9e830aa3eee3e33207176a45b:platform/kubernetes/cnpg-barman-cloud-plugin/kustomization.yaml'],text=True);expected=base64.b64decode(re.search(r'SIDECAR_IMAGE:\s*(\S+)',source).group(1)).decode();assert sidecar==expected;host=expected.split('/',1)[0]
 if host=='registry.vallery.net':sideProof=head(expected,'Basic '+auth);private=True
 else:assert host=='ghcr.io';sideProof=head(expected);private=False
 out={'verifiedAt':datetime.datetime.now(datetime.UTC).isoformat(),'credential':{'namespace':'weather','name':'zot-origin-cluster-pull','uid':secret['metadata']['uid'],'type':secret['type']},'cnpgMirror':pgProof,'injectedSidecar':{'matchesLivePluginConfigurationAndAcceptedSource':True,'isPrivateZotImage':private,'weatherCredentialSentToPublicRegistry':False,**sideProof},'newIdentityCreated':False,'newJobCreated':False,'credentialValuesEmitted':False};p=pathlib.Path('/Users/jason/maintenance/weather-cnpg-6/cnpg-registry-pull-authorization.json');p.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({'verified':True,'cnpgMirrorStatus':pgProof['status'],'cnpgDigest':pgProof['returnedDigest'],'sidecarRegistry':host,'sidecarStatus':sideProof['status'],'blobBytesDownloaded':0,'receipt':str(p)}))
try:main()
except Exception as error:
 print(json.dumps({'verified':False,'errorClass':type(error).__name__,'httpStatus':getattr(error,'code',None),'details':'suppressed'}));sys.exit(1)

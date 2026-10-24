import datetime,json,pathlib,subprocess
k=['kubectl','--context','vallery']
def get(*args):return json.loads(subprocess.check_output(k+list(args)))
def q(v):
 s=str(v);u={'n':1e-9,'u':1e-6,'m':1e-3,'Ki':1024,'Mi':1024**2,'Gi':1024**3,'Ti':1024**4}
 for key in sorted(u,key=len,reverse=True):
  if s.endswith(key):return float(s[:-len(key)])*u[key]
 return float(s)
nodes=get('get','nodes','-o','json')['items'];pods=get('get','pods','-A','-o','json')['items'];metrics=get('get','--raw','/apis/metrics.k8s.io/v1beta1/nodes')['items'];metric={n['metadata']['name']:n['usage'] for n in metrics}
rows=[]
for n in nodes:
 name=n['metadata']['name']
 if name not in {'vm-k3s-node5','vm-k3s-node6'}:continue
 conditions={c['type']:c['status'] for c in n['status']['conditions']};healthy=conditions.get('Ready')=='True' and all(conditions.get(c)=='False' for c in ('MemoryPressure','DiskPressure','PIDPressure')) and not n['spec'].get('unschedulable',False)
 assert healthy
 ps=[p for p in pods if p['spec'].get('nodeName')==name and p['status'].get('phase') not in {'Succeeded','Failed'}];requests={'cpu':0.,'memory':0.}
 for p in ps:
  for key in requests:
   regular=sum(q(c.get('resources',{}).get('requests',{}).get(key,'0')) for c in p['spec'].get('containers',[])); init=max([q(c.get('resources',{}).get('requests',{}).get(key,'0')) for c in p['spec'].get('initContainers',[])]+[0]);requests[key]+=max(regular,init)+q(p['spec'].get('overhead',{}).get(key,'0'))
 rows.append({'node':name,'readyNoPressureSchedulable':healthy,'allocatableCPU':q(n['status']['allocatable']['cpu']),'requestedCPU':round(requests['cpu'],3),'usedCPU':q(metric[name]['cpu']),'allocatableGi':round(q(n['status']['allocatable']['memory'])/2**30,2),'requestedGi':round(requests['memory']/2**30,2),'workingGi':round(q(metric[name]['memory'])/2**30,2),'activePods':len(ps)})
assert len(rows)==2
quotas=get('-n','agent-fleet-ci','get','resourcequota','-o','json')['items'];quota=[{'name':x['metadata']['name'],'hard':x['status']['hard'],'used':x['status'].get('used',{})} for x in quotas]
api=subprocess.check_output(k+['get','--raw','/readyz'],text=True).strip();assert api=='ok'
eventbus=[p for p in pods if p['metadata']['namespace']=='agent-fleet-ci' and 'eventbus' in p['metadata']['name']];eventready=sum(any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions',[])) for p in eventbus);assert eventready>=3
origin=[p for p in pods if 'zot' in p['metadata']['name'] and 'origin' in p['metadata']['name']];assert origin and all(any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions',[])) for p in origin)
lease=get('-n','kube-system','get','lease','k3s-planned-maintenance','-o','json')
holder=lease.get('spec',{}).get('holderIdentity','');assert not holder or 'vm-k3s-node7-' in holder
template=get('-n','agent-fleet-ci','get','workflowtemplate','repo-validate-events','-o','json')
terms=template['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
assert terms and all(any(e.get('key')=='kubernetes.io/hostname' and e.get('operator')=='NotIn' and 'vm-k3s-node7' in e.get('values',[]) for e in t.get('matchExpressions',[])) for t in terms)
out={'checkedAt':datetime.datetime.now(datetime.UTC).isoformat(),'context':'vallery','namespace':'agent-fleet-ci','submissionPath':'normal Weather event PR 3-check validation, existing NotIn node7 profile','ownerWholeMaintenanceTerminal':'https://github.com/jvallery/proxmox-infrastructure/issues/420#issuecomment-6383697168','nodes':rows,'quotas':quota,'apiReadyz':api,'eventbusReadyPods':eventready,'registryOriginReady':True,'maintenanceLeaseHolderEmpty':not bool(holder),'scopedMaintenanceExcludesOnlyNode7':True,'eventWrapperActuallyExcludesNode7':True,'ownerScopeReceipt':'/Users/jason/maintenance/wave1-20261024/inflight/VerdifyConsultancy__wordpress__243.json','noPlacementPolicyChange':True}
p=pathlib.Path('/Users/jason/maintenance/weather-cnpg-6/weather-ci-capacity-'+datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%SZ')+'.json');p.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))

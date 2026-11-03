"""Read-only CSV manifest budget; no SSH, DB writes, or data-file changes."""
import argparse,csv,json,math,os
from pathlib import Path

def assess(manifest,filesystem,reserve_gib=20,committed_gib=0):
 with Path(manifest).open(newline='',encoding='utf-8') as stream:
  rows=list(csv.DictReader(stream))
 if not rows:raise ValueError('Manifest is empty')
 names=set();source=working=0
 for row in rows:
  name=row['name']
  if not name or name in names:raise ValueError('Empty or duplicate volume name: '+name)
  names.add(name)
  shape=tuple(map(int,row['shape_zyx'].split('x')))
  if len(shape)!=3 or min(shape)<1:raise ValueError('Invalid ZYX shape')
  b=int(row['triplet_bytes']);w=int(row['working_label_uint16_bytes'])
  if b<1 or w!=math.prod(shape)*2:raise ValueError('Invalid bytes or uint16 working-label budget: '+name)
  if row.get('validation_errors','').strip():raise ValueError('Manifest contains validation errors: '+name)
  source+=b;working+=w
 if reserve_gib<0 or committed_gib<0 or not all(math.isfinite(x) for x in (reserve_gib,committed_gib)):raise ValueError('Budgets must be finite and nonnegative')
 anchor=Path(filesystem).resolve()
 while not anchor.exists():anchor=anchor.parent
 st=os.statvfs(anchor);free=st.f_bavail*st.f_frsize/2**30
 required=source/2**30+working/2**30+reserve_gib+committed_gib
 return {'write':False,'volumes':len(rows),'filesystem':str(anchor),'free_GiB':free,'source_GiB':source/2**30,'working_label_GiB':working/2**30,'reserve_GiB':reserve_gib,'other_committed_GiB':committed_gib,'required_GiB_before_snapshots_and_pyramids':required,'capacity_with_working_labels':free>=required,'shortfall_GiB':max(0,required-free),'note':'A current storage calculation, not source/header/access or authorization validation.'}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',required=True);p.add_argument('--filesystem',default='.');p.add_argument('--reserve-gib',type=float,default=20);p.add_argument('--committed-gib',type=float,default=0);a=p.parse_args()
 print(json.dumps(assess(a.manifest,a.filesystem,a.reserve_gib,a.committed_gib),indent=2))
if __name__=='__main__':main()

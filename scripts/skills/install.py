"""Install portable skill folders only; defaults to a preview and never overwrites differing content."""
import argparse,hashlib,json,os,shutil
from pathlib import Path

def inventory(root):
 return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}

def install_to(source,roots,apply=False):
 plans=[]
 for tool,root in roots.items():
  for skill in sorted(source.iterdir()):
   if not (skill/'SKILL.md').is_file():continue
   target=root/skill.name
   if target.is_symlink():raise ValueError('Refusing to replace skill symlink: '+str(target))
   same=target.exists() and inventory(skill)==inventory(target)
   if target.exists() and not same:raise ValueError('Existing skill differs; nothing overwritten: '+str(target))
   plans.append({'tool':tool,'source':str(skill),'destination':str(target),'action':'keep-identical' if same else 'install'})
 if apply:
  for plan in plans:
   src=Path(plan['source']);dst=Path(plan['destination'])
   if plan['action']=='install':
    dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(src,dst,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
   assert inventory(src)==inventory(dst),'Installed skill verification failed'
 return {'applied':apply,'skills':plans,'verified':bool(apply),'runtime_discovery_tested':False}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--tool',choices=['codex','claude','antigravity','all'],required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
 home=Path.home();roots={'codex':Path(os.environ.get('CODEX_HOME',str(home/'.codex')))/'skills','claude':home/'.claude/skills','antigravity':home/'.gemini/config/skills'}
 if a.tool!='all':roots={a.tool:roots[a.tool]}
 here=Path(__file__).resolve()
 source=here.parent/'skills' if (here.parent/'skills').is_dir() else here.parents[2]/'skills'
 print(json.dumps(install_to(source,roots,a.apply),indent=2))
if __name__=='__main__':main()

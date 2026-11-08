"""Exercise a running backend and save an export and comparison report."""
import argparse
import json
from pathlib import Path
import urllib.request

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:8000')
parser.add_argument('--output',default='experiments/demo.json')
args=parser.parse_args()
def call(path, body=None):
    data=json.dumps(body).encode() if body is not None else None
    request=urllib.request.Request(args.url+path,data=data,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=180) as response:
        return json.load(response)
body=json.loads((Path(__file__).resolve().parents[1]/'data/samples/delivery.json').read_text())
mission=call('/api/missions',body)
routes=call(f'/api/missions/{mission["id"]}/routes',{'count':3})
if not routes['routes']: raise SystemExit(routes['reason'])
batch=call('/api/simulations',{'mission_id':mission['id'],'weather_ids':['weak','moderate','strong'],'repeats':3,'seed':42})
comparison=call('/api/evaluations/compare',{'experiment_id':batch['id']})
replay=call(f'/api/experiments/{batch["id"]}/replay',{})
export=call(f'/api/experiments/{batch["id"]}/export')
output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps({'routes':routes,'comparison':comparison,'replay':replay,'experiment':export},ensure_ascii=False,indent=2))
print(json.dumps({'routes':len(routes['routes']),'runs':len(batch['runs']),'replay':replay,'comparison':comparison,'output':str(output)},ensure_ascii=False,indent=2))

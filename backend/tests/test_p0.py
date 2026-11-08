import json
import os
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.schemas import Environment, Mission, Weather
from app.engine import generate, route_safe, simulate, compare, segment_box_distance

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(os.getenv("TEST_DATABASE_URL",f"sqlite:///{tmp_path}/test.db"))) as c:
        yield c

def mission_body(kind="delivery"):
    body={"environment_id":"demo-city","kind":kind,"start":{"x":10,"y":20,"z":20},"goal":{"x":150,"y":80,"z":20}}
    if kind!="delivery": body["targets"]=[{"x":30,"y":40,"z":20}]
    return body

def test_end_to_end(client):
    assert client.get('/health').status_code==200
    mid=client.post('/api/missions',json=mission_body()).json()['id']
    routes=client.post(f'/api/missions/{mid}/routes',json={"count":3}).json()
    assert routes['generated_count']==3, routes
    for route in routes['routes']:
        assert route['distance_m']>0 and len(route['points'])==len(route['wgs84'])
    batch=client.post('/api/simulations',json={"mission_id":mid,"weather_ids":["weak","moderate","strong"],"repeats":2}).json()
    assert len(batch['runs'])==18
    assert len({r['metrics']['time_s'] for r in batch['runs']})>3
    eid=batch['id']
    assert client.get(f'/api/experiments/{eid}').json()['input_hash']==batch['input_hash']
    assert client.post(f'/api/experiments/{eid}/replay').json()['matches']
    result=client.post('/api/evaluations/compare',json={"experiment_id":eid}).json()
    assert len(result['scenario_results'])==3
    assert client.get(f'/api/experiments/{eid}/export?format=csv').text.count('\n')==19
    assert client.get(f'/api/experiments/{eid}/export').json()['snapshot']
    voxels=client.get('/api/environments/demo-city/voxels?limit=2').json()
    assert len(voxels['cells'])==2 and voxels['total']>2

@pytest.mark.parametrize('kind',['inspection','disaster','delivery'])
def test_mission_kinds(client,kind):
    body=mission_body(kind)
    m=Mission.model_validate(body)
    env=Environment.model_validate({k:v for k,v in client.get('/api/environments/demo-city').json().items() if k!='id'})
    routes,reason=generate(env,m,3)
    assert routes,reason
    for r in routes:
        assert route_safe(r['points'],env,m)
        for p in m.targets: assert (p.x,p.y,p.z) in r['points']

def test_blocked_and_invalid(client):
    body=mission_body(); body['start']={"x":80,"y":80,"z":20}
    mid=client.post('/api/missions',json=body).json()['id']
    result=client.post(f'/api/missions/{mid}/routes',json={}).json()
    assert result['generated_count']==0 and result['reason']
    assert client.post('/api/simulations',json={"mission_id":mid,"weather_ids":["weak"]}).status_code==409
    body=mission_body(); body['airspeed_mps']=0
    assert client.post('/api/missions',json=body).status_code==422
    body=mission_body('inspection'); body['targets']=[]
    assert client.post('/api/missions',json=body).status_code==422
    assert client.get('/api/missions/missing').status_code==404

def test_geometry_weather_and_reproducibility():
    env=Environment(name="wall",size={"x":60,"y":60,"z":40},obstacles=[{"minimum":{"x":25,"y":0,"z":0},"maximum":{"x":35,"y":60,"z":40}}])
    m=Mission(environment_id="wall",kind="delivery",start={"x":10,"y":10,"z":20},goal={"x":50,"y":10,"z":20},max_altitude_m=40)
    assert generate(env,m,3)[0]==[]
    assert not route_safe([(10,10,20),(50,10,20)],env,m)
    assert segment_box_distance((10,10,20),(50,10,20),env.obstacles[0])==0
    env.obstacles=[]
    points=[(10,10,20),(50,10,20)]
    calm=Weather(name='calm',wind_speed_mps=0,wind_from_deg=0,timestamp='2026-11-05T00:00:00Z',source='synthetic')
    windy=calm.model_copy(update={"wind_speed_mps":5,"wind_from_deg":90})
    a=simulate(points,env,m,calm,42); b=simulate(points,env,m,windy,42)
    assert a['safe'] and b['time_s']>a['time_s'] and b['energy_wh']>a['energy_wh']
    assert b==simulate(points,env,m,windy,42)
    extreme=windy.model_copy(update={"wind_speed_mps":40})
    failed=simulate(points,env,m,extreme,42)
    assert not failed['safe']
    assert compare([{"route_id":"r","metrics":failed}],"delivery")['recommended_route_id'] is None

def test_uncertainty_and_turn():
    from app.engine import risk_at
    env=Environment(name='risk',size={"x":60,"y":60,"z":40},risk_fields=[{"minimum":{"x":0,"y":0,"z":0},"maximum":{"x":30,"y":30,"z":30},"risk":.3,"uncertainty":.4}])
    assert risk_at((10,10,20),env)==pytest.approx(.7)
    m=Mission.model_validate(mission_body())
    assert not route_safe([(10,10,20),(20,10,20),(10,10,20)],env,m)

def test_budget_and_safe_recommendation():
    env=Environment(name='empty',size={"x":60,"y":60,"z":40})
    m=Mission.model_validate(mission_body()).model_copy(update={"battery_wh":.001})
    weather=Weather(name='calm',wind_speed_mps=0,wind_from_deg=0,timestamp='2026-11-05T00:00:00Z',source='synthetic')
    bad=simulate([(10,10,20),(50,10,20)],env,m,weather,0)
    good=simulate([(10,10,20),(50,10,20)],env,m.model_copy(update={"battery_wh":150}),weather,0)
    assert 'battery_budget' in bad['failure_reasons']
    result=compare([{"route_id":"bad","metrics":bad},{"route_id":"good","metrics":good}],"delivery")
    assert result['recommended_route_id']=='good'
    assert result['excluded_route_ids']==['bad']

def test_clearance_no_fly_and_coordinates():
    from app.engine import geodetic, valid_segment
    env=Environment(name='restricted',size={"x":60,"y":60,"z":40},no_fly_zones=[{"minimum":{"x":25,"y":25,"z":0},"maximum":{"x":35,"y":35,"z":40}}])
    m=Mission.model_validate(mission_body())
    assert not valid_segment((10,20,20),(50,20,20),env,m)  # exactly 5m clearance is rejected conservatively
    assert valid_segment((10,10,20),(50,10,20),env,m)
    origin=geodetic((0,0,0),env)
    assert origin['latitude']==pytest.approx(env.origin_lat,abs=1e-8)
    assert origin['longitude']==pytest.approx(env.origin_lon,abs=1e-8)
    assert origin['altitude']==pytest.approx(env.origin_alt,abs=1e-5)

@pytest.mark.parametrize('field',['start','goal'])
def test_blocked_endpoints(client,field):
    body=mission_body(); body[field]={"x":80,"y":80,"z":20}
    mid=client.post('/api/missions',json=body).json()['id']
    result=client.post(f'/api/missions/{mid}/routes',json={}).json()
    assert result['routes']==[] and result['reason']

def test_persistence_restart(tmp_path):
    url=f'sqlite:///{tmp_path}/persistent.db'
    with TestClient(create_app(url)) as client:
        mid=client.post('/api/missions',json=mission_body()).json()['id']
    with TestClient(create_app(url)) as client:
        assert client.get(f'/api/missions/{mid}').json()['kind']=='delivery'

def test_five_distinct_candidates_deterministic():
    env=Environment(name='open',size={"x":80,"y":80,"z":40})
    m=Mission(environment_id='open',kind='delivery',start={"x":10,"y":10,"z":20},goal={"x":70,"y":70,"z":20})
    routes,reason=generate(env,m,5)
    assert len(routes)==5 and reason is None
    assert len({tuple(r['points']) for r in routes})==5
    assert all(route_safe(r['points'],env,m) for r in routes)
    assert generate(env,m,5)[0]==routes

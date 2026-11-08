import csv
import hashlib
import io
import json
import os
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from .schemas import Environment, Mission, Weather, RouteRequest, SimulationRequest, CompareRequest
from .store import Store, PlanningRow, EnvironmentRow, MissionRow, WeatherRow, RouteRow, ExperimentRow, RunRow, EvaluationRow
from .engine import VERSION, generate, geodetic, simulate, compare


def digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(",", ":")).encode()).hexdigest()

def code_hash():
    return digest({p.name:p.read_text() for p in sorted(Path(__file__).parent.glob("*.py"))})

def create_app(database_url=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(database_url)
        seed(app.state.store)
        yield
        app.state.store.engine.dispose()
    app = FastAPI(title="MissionDrone P0", version=VERSION, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS","http://localhost:5173").split(","), allow_methods=["GET","POST"],allow_headers=["*"])
    def store(): return app.state.store
    def require(cls,id):
        row = store().get(cls,id)
        if row is None: raise HTTPException(404,"record not found")
        return row
    def parsed(cls, row): return cls.model_validate({k:v for k,v in row.items() if k!="id"})

    @app.get("/health")
    def health():
        store().all(EnvironmentRow)
        return {"status":"ok","version":VERSION}

    @app.get("/api/environments")
    def environments(): return store().all(EnvironmentRow)

    @app.post("/api/environments", status_code=201)
    def add_environment(body: Environment): return store().put(EnvironmentRow,body.model_dump())

    @app.get("/api/environments/{id}")
    def environment(id: str): return require(EnvironmentRow,id)

    @app.get("/api/environments/{id}/voxels")
    def voxels(id: str, offset: int=Query(0,ge=0), limit: int=Query(1000,ge=1,le=5000)):
        from .engine import box_distance, risk_at
        import itertools
        env = parsed(Environment,require(EnvironmentRow,id))
        cells=[]; total=0
        for p in itertools.product(*(range(int(getattr(env.size,a)/env.resolution)+1) for a in "xyz")):
            point=tuple(v*env.resolution for v in p)
            occupied=any(box_distance(point,b)==0 for b in env.obstacles)
            prohibited=any(box_distance(point,b)==0 for b in env.no_fly_zones)
            risk=risk_at(point,env)
            if not (occupied or prohibited or risk): continue
            if offset <= total < offset+limit:
                cells.append({"position":point,"wgs84":geodetic(point,env),"occupied":occupied,"no_fly":prohibited,"risk":risk})
            total+=1
        return {"cells":cells,"total":total,"offset":offset,"resolution_m":env.resolution,"model_version":env.model_version}

    @app.post("/api/missions",status_code=201)
    def add_mission(body: Mission):
        require(EnvironmentRow,body.environment_id)
        return store().put(MissionRow,body.model_dump(),environment_id=body.environment_id)

    @app.get("/api/missions/{id}")
    def mission(id: str): return require(MissionRow,id)

    @app.post("/api/missions/{id}/routes")
    def routes(id: str, body: RouteRequest):
        m=parsed(Mission,require(MissionRow,id)); env=parsed(Environment,require(EnvironmentRow,m.environment_id))
        candidates,reason=generate(env,m,body.count)
        results=[]
        generation_id=str(uuid.uuid4())
        generated_at=datetime.now(timezone.utc).isoformat()
        with store().sessions.begin() as session:
            store().put(PlanningRow,{"generated_at":generated_at,"reason":reason,"count":len(candidates)},session=session,id=generation_id,mission_id=id)
            for route in candidates:
                route["wgs84"]=[geodetic(p,env) for p in route["points"]]
                route["generation_id"]=generation_id
                route["generated_at"]=generated_at
                route["planner_version"]=VERSION
                route["input_hash"]=digest({"environment":env.model_dump(),"mission":m.model_dump()})
                results.append(store().put(RouteRow,route,session=session,mission_id=id))
        return {"routes":results,"requested_count":body.count,"generated_count":len(results),"reason":reason}

    @app.get("/api/missions/{id}/routes")
    def get_routes(id: str):
        require(MissionRow,id)
        return latest_routes(id)

    def latest_routes(mission_id):
        generations=store().all(PlanningRow,mission_id=mission_id)
        if not generations: return []
        latest=max(generations,key=lambda r:r["generated_at"])["id"]
        return [r for r in store().all(RouteRow,mission_id=mission_id) if r["generation_id"]==latest]

    @app.get("/api/weather-scenarios")
    def weather(): return store().all(WeatherRow)

    @app.post("/api/weather-scenarios",status_code=201)
    def add_weather(body: Weather): return store().put(WeatherRow,body.model_dump())

    def experiment(id):
        row=require(ExperimentRow,id)
        row["runs"]=store().all(RunRow,experiment_id=id)
        row["evaluations"]=store().all(EvaluationRow,experiment_id=id)
        return row

    @app.post("/api/simulations",status_code=201)
    def simulations(body: SimulationRequest):
        m=parsed(Mission,require(MissionRow,body.mission_id))
        env=parsed(Environment,require(EnvironmentRow,m.environment_id))
        weathers=[require(WeatherRow,id) for id in dict.fromkeys(body.weather_ids)]
        candidates=latest_routes(body.mission_id)
        if not candidates: raise HTTPException(409,"generate routes before simulation")
        snapshot={"environment":env.model_dump(),"mission":m.model_dump(),"routes":candidates,"weather":weathers}
        payload={"status":"completed","config":body.model_dump(),"simulator_version":VERSION,
                 "code_hash":code_hash(),"created_at":datetime.now(timezone.utc).isoformat(),"input_hash":digest(snapshot),"snapshot":snapshot,
                 "data_kind":"simulation","units":{"distance":"m","time":"s","energy":"Wh","coordinates":"local ENU"}}
        with store().sessions.begin() as session:
            batch=store().put(ExperimentRow,payload,session=session,mission_id=body.mission_id)
            for route in candidates:
                for w in weathers:
                    for repeat in range(body.repeats):
                        run_seed=body.seed+repeat
                        metrics=simulate(route["points"],env,m,parsed(Weather,w),run_seed)
                        store().put(RunRow,{"route_id":route["id"],"weather_id":w["id"],"repeat":repeat,"seed":run_seed,"metrics":metrics},session=session,experiment_id=batch["id"],route_id=route["id"],weather_id=w["id"])
        return experiment(batch["id"])

    @app.get("/api/simulations/{id}")
    def simulation(id: str): return experiment(id)

    @app.post("/api/evaluations/compare")
    def evaluate(body: CompareRequest):
        exp=experiment(body.experiment_id)
        result=compare(exp["runs"],exp["snapshot"]["mission"]["kind"])
        result["scenario_results"]={wid:compare([r for r in exp["runs"] if r["weather_id"]==wid],exp["snapshot"]["mission"]["kind"]) for wid in {r["weather_id"] for r in exp["runs"]}}
        return store().put(EvaluationRow,result,experiment_id=body.experiment_id)

    @app.get("/api/experiments/{id}")
    def get_experiment(id: str): return experiment(id)

    @app.post("/api/experiments/{id}/replay")
    def replay(id: str):
        exp=experiment(id); snap=exp["snapshot"]
        if exp["simulator_version"] != VERSION or exp["code_hash"] != code_hash(): raise HTTPException(409,"simulator version mismatch")
        env=Environment.model_validate(snap["environment"]); m=Mission.model_validate(snap["mission"])
        routes={r["id"]:r for r in snap["routes"]}; weathers={w["id"]:parsed(Weather,w) for w in snap["weather"]}
        matching=all(simulate(routes[r["route_id"]]["points"],env,m,weathers[r["weather_id"]],r["seed"])==r["metrics"] for r in exp["runs"])
        return {"matches":matching,"run_count":len(exp["runs"]),"input_hash_valid":digest(snap)==exp["input_hash"],"simulator_version":VERSION}

    @app.get("/api/experiments/{id}/export")
    def export(id: str, format: str=Query("json",pattern="^(json|csv)$")):
        exp=experiment(id)
        if format=="json": return exp
        buffer=io.StringIO(); writer=csv.writer(buffer)
        keys=["safe","distance_m","time_s","energy_wh","risk_exposure_s","risk_voxel_ratio","coverage","minimum_clearance_m","arrived","failure_reasons"]
        writer.writerow(["experiment_id","route_id","weather_id","repeat","seed"]+keys)
        for r in exp["runs"]:
            writer.writerow([id,r["route_id"],r["weather_id"],r["repeat"],r["seed"]]+[r["metrics"][k] for k in keys])
        return Response(buffer.getvalue(),media_type="text/csv",headers={"Content-Disposition":f'attachment; filename="{id}.csv"'})
    return app


def seed(store):
    if not store.get(EnvironmentRow,"demo-city"):
        p=lambda x,y,z:dict(x=x,y=y,z=z)
        env=Environment.model_validate({"name":"Synthetic Seoul block","size":p(160,160,80),"resolution":10,
            "obstacles":[{"minimum":p(60,55,0),"maximum":p(100,105,55)}],
            "no_fly_zones":[{"minimum":p(120,110,0),"maximum":p(145,140,80)}],
            "risk_fields":[{"minimum":p(30,100,0),"maximum":p(100,150,80),"risk":.5,"uncertainty":.2}]})
        store.put(EnvironmentRow,env.model_dump(),id="demo-city")
    for name,speed,direction in [("weak",2,270),("moderate",6,180),("strong",12,90)]:
        if not store.get(WeatherRow,name):
            w=Weather(name=name,wind_speed_mps=speed,wind_from_deg=direction,timestamp="2026-11-05T00:00:00Z",source="synthetic scenario; not observed weather")
            store.put(WeatherRow,w.model_dump(),id=name)

app=create_app()

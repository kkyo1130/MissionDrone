"""Bounded local ENU planner and explicitly approximate uniform-wind simulator."""
import heapq
import itertools
import math
import random
from .schemas import Environment, Mission, Point, Weather

VERSION = "p0-1.0"

def xyz(p):
    return (p.x, p.y, p.z)

def distance(a, b):
    return math.dist(a, b)

def box_distance(p, box):
    return math.sqrt(sum(max(getattr(box.minimum, a) - v, 0, v - getattr(box.maximum, a)) ** 2 for a, v in zip("xyz", p)))

def segment_box_distance(a, b, box):
    # Exact piecewise quadratic minimum, including corner/edge contacts.
    delta = tuple(y-x for x,y in zip(a,b))
    breaks = {0.,1.}
    for i,axis in enumerate("xyz"):
        if delta[i]:
            for bound in (getattr(box.minimum,axis),getattr(box.maximum,axis)):
                t=(bound-a[i])/delta[i]
                if 0<t<1: breaks.add(t)
    times=sorted(breaks)
    candidates=list(times)
    for lo,hi in zip(times,times[1:]):
        mid=(lo+hi)/2
        aa=bb=0.
        for i,axis in enumerate("xyz"):
            value=a[i]+mid*delta[i]
            lower,upper=getattr(box.minimum,axis),getattr(box.maximum,axis)
            bound=lower if value<lower else upper if value>upper else None
            if bound is not None:
                aa+=delta[i]**2
                bb+=delta[i]*(a[i]-bound)
        if aa: candidates.append(max(lo,min(hi,-bb/aa)))
    return min(box_distance(tuple(x+t*d for x,d in zip(a,delta)),box) for t in candidates)

def risk_at(p, env):
    return max((min(1., b.risk + b.uncertainty) for b in env.risk_fields if box_distance(p, b) == 0), default=0.)

def valid_point(p, env, mission):
    margin = mission.clearance_m + mission.drone_radius_m + mission.position_error_m
    return (all(0 <= v <= getattr(env.size, a) for a, v in zip("xyz", p))
            and mission.min_altitude_m <= p[2] <= mission.max_altitude_m
            and all(box_distance(p, b) > margin for b in env.obstacles + env.no_fly_zones))

def valid_segment(a, b, env, m):
    if not valid_point(a, env, m) or not valid_point(b, env, m):
        return False
    d = distance(a, b)
    if d and abs(b[2]-a[2]) / d * m.airspeed_mps > m.max_climb_mps + 1e-9:
        return False
    margin = m.clearance_m + m.drone_radius_m + m.position_error_m
    return all(segment_box_distance(a, b, box) > margin + 1e-6 for box in env.obstacles + env.no_fly_zones)

def turn_ok(a, b, c, m):
    u, v = tuple(y-x for x,y in zip(a,b)), tuple(y-x for x,y in zip(b,c))
    den = math.sqrt(sum(x*x for x in u)*sum(x*x for x in v))
    return not den or math.degrees(math.acos(max(-1, min(1, sum(x*y for x,y in zip(u,v))/den)))) <= m.max_turn_deg + 1e-8

def route_safe(points, env, m):
    return (len(points) >= 2 and all(valid_segment(a,b,env,m) for a,b in zip(points,points[1:]))
            and all(turn_ok(a,b,c,m) for a,b,c in zip(points,points[1:],points[2:])))

def astar(start, goal, env, m, used, variant):
    res = env.resolution
    # Include exact anchors in each axis: no unchecked snapping connectors.
    axes = [sorted(set([i*res for i in range(int(getattr(env.size,a)/res)+1)] + [start[j], goal[j]])) for j,a in enumerate("xyz")]
    s = tuple(axes[i].index(start[i]) for i in range(3))
    g = tuple(axes[i].index(goal[i]) for i in range(3))
    def pos(n): return tuple(axes[i][n[i]] for i in range(3))
    initial = (s, None)
    queue = [(distance(start,goal), 0., 0, initial)]
    costs, parent = {initial: 0.}, {}
    serial = itertools.count(1)
    cache = {}
    expanded = 0
    while queue:
        _, cost, _, state = heapq.heappop(queue)
        if cost != costs.get(state): continue
        node, previous = state
        p = pos(node)
        if node == g:
            path = []
            while state in parent:
                path.append(pos(state[0])); state = parent[state]
            return [start] + path[::-1]
        expanded += 1
        if expanded > 100000: return None
        for delta in itertools.product((-1,0,1), repeat=3):
            if delta == (0,0,0): continue
            nxt = tuple(node[i]+delta[i] for i in range(3))
            if any(nxt[i]<0 or nxt[i]>=len(axes[i]) for i in range(3)): continue
            q = pos(nxt)
            key = (node,nxt)
            if key not in cache: cache[key] = valid_segment(p,q,env,m)
            if not cache[key] or (previous is not None and not turn_ok(pos(previous),p,q,m)): continue
            d = distance(p,q)
            penalty = used.get(tuple(round(v,3) for v in q),0)
            newcost = cost + d * (1 + (2+variant%3)*risk_at(q,env) + variant*.7*penalty)
            ns = (nxt,node)
            if newcost < costs.get(ns,float("inf")):
                costs[ns] = newcost; parent[ns] = state
                heapq.heappush(queue,(newcost+distance(q,goal),newcost,next(serial),ns))
    return None

def generate(env: Environment, m: Mission, count: int):
    anchors = [xyz(m.start)] + [xyz(p) for p in m.waypoints]
    if m.kind != "delivery": anchors += [xyz(p) for p in m.targets]
    anchors += [xyz(m.goal)]
    if any(not valid_point(p,env,m) for p in anchors):
        return [], "출발지·도착지·필수 경유지가 차단되었거나 고도/환경 범위를 벗어났습니다."
    routes, used = [], {}
    for variant in range(count*4):
        path = [anchors[0]]
        for a,b in zip(anchors,anchors[1:]):
            leg = astar(a,b,env,m,used,variant)
            if leg is None: break
            path += leg[1:]
        else:
            if not route_safe(path,env,m): continue
            cells = set(path[1:-1])
            if any(len(cells & set(r["points"][1:-1])) / max(1,len(cells | set(r["points"][1:-1]))) > .85 for r in routes):
                continue
            routes.append({"points": path, "distance_m":sum(distance(a,b) for a,b in zip(path,path[1:])),
                           "risk_score":max(risk_at(p,env) for p in path)})
            for p in path: used[p] = used.get(p,0)+1
            if len(routes) == count: break
    return routes, None if len(routes)==count else "안전·비행 제약과 중복 제거 후 가능한 후보 수가 요청보다 적습니다."

def geodetic(p, env):
    # WGS84 geodetic -> ECEF origin, ENU rotation, ECEF -> geodetic.
    a, e2 = 6378137., 6.69437999014e-3
    lat, lon = math.radians(env.origin_lat), math.radians(env.origin_lon)
    sl,cl,so,co = math.sin(lat),math.cos(lat),math.sin(lon),math.cos(lon)
    n = a/math.sqrt(1-e2*sl*sl)
    east,north,up = p
    x=(n+env.origin_alt)*cl*co-so*east-sl*co*north+cl*co*up
    y=(n+env.origin_alt)*cl*so+co*east-sl*so*north+cl*so*up
    z=(n*(1-e2)+env.origin_alt)*sl+cl*north+sl*up
    radius=math.hypot(x,y)
    phi=math.atan2(z,radius*(1-e2))
    for _ in range(10):
        n=a/math.sqrt(1-e2*math.sin(phi)**2)
        phi=math.atan2(z+e2*n*math.sin(phi),radius)
    n=a/math.sqrt(1-e2*math.sin(phi)**2)
    altitude=radius/math.cos(phi)-n
    return {"longitude":math.degrees(math.atan2(y,x)),"latitude":math.degrees(phi),"altitude":altitude}

def simulate(points, env, m, weather: Weather, seed):
    rng = random.Random(seed)
    speed = max(0, weather.wind_speed_mps + rng.uniform(-.05,.05)*weather.wind_speed_mps)
    rad = math.radians(weather.wind_from_deg)
    wind = (-speed*math.sin(rad),-speed*math.cos(rad),0.)
    duration = energy = exposure = risky_distance = length = 0.
    failures = []
    if not route_safe(points,env,m): failures.append("geometry_or_flight_constraint")
    for a,b in zip(points,points[1:]):
        d = distance(a,b)
        if not d: continue
        direction = tuple((y-x)/d for x,y in zip(a,b))
        along = sum(x*y for x,y in zip(wind,direction))
        cross2 = max(0, speed*speed-along*along)
        if cross2 >= m.airspeed_mps**2:
            failures.append("wind_exceeds_track_hold"); break
        ground = math.sqrt(m.airspeed_mps**2-cross2)+along
        if ground <= .5:
            failures.append("insufficient_ground_speed"); break
        seconds = d/ground
        if abs(b[2]-a[2])/seconds > m.max_climb_mps+1e-9: failures.append("climb_rate")
        samples = max(1,math.ceil(d/(env.resolution/2)))
        risk = sum(risk_at(tuple(x+(y-x)*(i+.5)/samples for x,y in zip(a,b)),env) for i in range(samples))/samples
        voxel_risk = risk
        risk = min(1,risk + speed/60*.2)
        duration += seconds
        energy += (180+25*m.payload_kg+4*speed*speed+max(0,direction[2])*100)*seconds/3600
        exposure += risk*seconds
        risky_distance += d*(voxel_risk>.2)
        length += d
    if energy > m.battery_wh: failures.append("battery_budget")
    if duration > m.time_limit_s: failures.append("time_budget")
    targets = [xyz(p) for p in m.targets]
    coverage = sum(any(distance(t,p)<=m.observation_radius_m for p in points) for t in targets)/len(targets) if targets else 1.
    if coverage < 1: failures.append("mission_coverage")
    clearance = min((segment_box_distance(a,b,box) for a,b in zip(points,points[1:]) for box in env.obstacles+env.no_fly_zones), default=None)
    return {"safe":not failures,"failure_reasons":sorted(set(failures)),"distance_m":length,"time_s":duration,
            "energy_wh":energy,"risk_exposure_s":exposure,"risk_voxel_ratio":risky_distance/max(length,1),
            "coverage":coverage,"minimum_clearance_m":clearance,"arrived":not failures}

def compare(runs, kind):
    grouped = {}
    for run in runs: grouped.setdefault(run["route_id"],[]).append(run)
    eligible = {k:v for k,v in grouped.items() if all(r["metrics"]["safe"] for r in v)}
    weights = {"delivery":(.2,.4,.3,.1),"inspection":(.1,.2,.2,.5),"disaster":(.1,.5,.1,.3)}[kind]
    keys = ["distance_m","time_s","energy_wh","risk_exposure_s"]
    means = {k:[sum(r["metrics"][key] for r in v)/len(v) for key in keys] for k,v in eligible.items()}
    scores = {}
    for k, vals in means.items():
        score = 0
        for i,w in enumerate(weights):
            low,high = min(v[i] for v in means.values()),max(v[i] for v in means.values())
            score += w*((vals[i]-low)/(high-low) if high>low else 0)
        scores[k] = max(0.,min(100.,round(100*(1-score),6)))
    winner = max(scores,key=scores.get) if scores else None
    return {"recommended_route_id":winner,"scores":scores,"excluded_route_ids":sorted(set(grouped)-set(eligible)),
            "reason":"모든 선택 기상·반복에서 안전 및 예산을 통과한 후보의 임무별 정규화 점수 비교" if winner else "모든 선택 기상에서 제약을 충족하는 후보가 없습니다.",
            "weights":dict(zip(keys,weights))}

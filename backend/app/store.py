import os
import uuid
from sqlalchemy import JSON, ForeignKey, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

class Base(DeclarativeBase): pass

class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)

class EnvironmentRow(Record, Base):
    __tablename__ = "environments"
class MissionRow(Record, Base):
    __tablename__ = "missions"
    environment_id: Mapped[str] = mapped_column(ForeignKey("environments.id"))
class WeatherRow(Record, Base):
    __tablename__ = "weather_scenarios"
class PlanningRow(Record, Base):
    __tablename__ = "route_generations"
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"))
class RouteRow(Record, Base):
    __tablename__ = "route_candidates"
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"))
class ExperimentRow(Record, Base):
    __tablename__ = "experiment_batches"
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"))
class RunRow(Record, Base):
    __tablename__ = "simulation_runs"
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiment_batches.id"))
    route_id: Mapped[str] = mapped_column(ForeignKey("route_candidates.id"))
    weather_id: Mapped[str] = mapped_column(ForeignKey("weather_scenarios.id"))
class EvaluationRow(Record, Base):
    __tablename__ = "evaluation_results"
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiment_batches.id"))

class Store:
    def __init__(self, url=None):
        url = url or os.getenv("DATABASE_URL", "sqlite:///./missiondrone.db")
        self.engine = create_engine(url, **({"connect_args":{"check_same_thread":False}} if url.startswith("sqlite") else {"pool_pre_ping":True}))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)

    def put(self, cls, payload, session=None, id=None, **links):
        own = session is None
        session = session or self.sessions()
        try:
            id = id or str(uuid.uuid4())
            session.add(cls(id=id,payload=payload,**links))
            if own: session.commit()
            else: session.flush()
            return {"id":id,**payload}
        finally:
            if own: session.close()

    def get(self, cls, id):
        with self.sessions() as s:
            row = s.get(cls,id)
            return {"id":row.id,**row.payload} if row else None

    def all(self, cls, **filters):
        with self.sessions() as s:
            return [{"id":r.id,**r.payload} for r in s.scalars(select(cls).filter_by(**filters).order_by(cls.id))]

"""SQLAlchemy persistence for missions, route results and repeatable experiments."""
from __future__ import annotations

import os
from sqlalchemy import JSON, ForeignKey, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class MissionRow(Base):
    __tablename__ = "missions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class RouteRow(Base):
    __tablename__ = "route_generations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class ExperimentRow(Base):
    __tablename__ = "experiment_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class Store:
    def __init__(self, url: str | None = None):
        url = url or os.getenv("DATABASE_URL", "sqlite:///./missiondrone.db")
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        self.engine = create_engine(url, **kwargs)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def save_mission(self, mission: dict) -> None:
        with self.sessions.begin() as session:
            session.merge(MissionRow(id=mission["id"], payload=mission))

    def get_mission(self, mission_id: str) -> dict | None:
        with self.sessions() as session:
            row = session.get(MissionRow, mission_id)
            return row.payload if row else None

    def save_routes(self, mission_id: str, result: dict) -> None:
        with self.sessions.begin() as session:
            session.merge(RouteRow(id=mission_id, mission_id=mission_id, payload=result))

    def get_routes(self, mission_id: str) -> dict | None:
        with self.sessions() as session:
            row = session.get(RouteRow, mission_id)
            return row.payload if row else None

    def save_experiment(self, result: dict) -> None:
        with self.sessions.begin() as session:
            session.add(ExperimentRow(id=result["id"], mission_id=result["mission_id"], payload=result))

    def get_experiment(self, experiment_id: str) -> dict | None:
        with self.sessions() as session:
            row = session.get(ExperimentRow, experiment_id)
            return row.payload if row else None

    def list_experiments(self, mission_id: str) -> list[dict]:
        with self.sessions() as session:
            rows = session.scalars(select(ExperimentRow).where(ExperimentRow.mission_id == mission_id).order_by(ExperimentRow.id)).all()
            return [row.payload for row in rows]

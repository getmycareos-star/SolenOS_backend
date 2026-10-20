"""CRUD operations for Routine Moment model."""

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.models.routine_moment import RoutineMoment
from app.schemas.routine_moment import RoutineMomentCreate, RoutineMomentUpdate


def get_routine_moment(db: Session, routine_moment_id: str) -> Optional[RoutineMoment]:
    return db.query(RoutineMoment).filter(RoutineMoment.id == routine_moment_id).first()


def get_routine_moments_by_person(
    db: Session, person_id: str, skip: int = 0, limit: int = 100
) -> List[RoutineMoment]:
    return (
        db.query(RoutineMoment)
        .filter(RoutineMoment.person_id == person_id)
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_routine_moments_by_person_and_routine(
    db: Session, person_id: str, routine_name: str, skip: int = 0, limit: int = 100
) -> List[RoutineMoment]:
    return (
        db.query(RoutineMoment)
        .filter(
            and_(
                RoutineMoment.person_id == person_id,
                RoutineMoment.routine_name == routine_name,
            )
        )
        .offset(skip)
        .limit(limit)
        .all()
    )


def create_routine_moment(db: Session, routine_moment: RoutineMomentCreate) -> RoutineMoment:
    db_routine_moment = RoutineMoment(
        **routine_moment.dict(exclude_unset=True),
        created_by_caregiver_id=routine_moment.created_by_caregiver_id,  # This should be passed in
    )
    db.add(db_routine_moment)
    db.commit()
    db.refresh(db_routine_moment)
    return db_routine_moment


def update_routine_moment(
    db: Session, routine_moment_id: str, routine_moment: RoutineMomentUpdate
) -> Optional[RoutineMoment]:
    db_routine_moment = get_routine_moment(db, routine_moment_id)
    if db_routine_moment:
        update_data = routine_moment.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_routine_moment, field, value)
        db.commit()
        db.refresh(db_routine_moment)
    return db_routine_moment


def delete_routine_moment(db: Session, routine_moment_id: str) -> bool:
    db_routine_moment = get_routine_moment(db, routine_moment_id)
    if db_routine_moment:
        db.delete(db_routine_moment)
        db.commit()
        return True
    return False
from __future__ import annotations


from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_owned_project
from app.db.models import Project, User
from app.db.session import get_db
from app.schemas.projects import ProjectCreate, ProjectList, ProjectOut, ProjectUpdate
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Project:
    return project_service.create(db, user, title=body.title, topic=body.topic)


@router.get("", response_model=ProjectList)
def list_projects(include_archived: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  user: User = Depends(current_user), db: Session = Depends(get_db)) -> ProjectList:
    items, total = project_service.list_(db, user, include_archived=include_archived, limit=limit, offset=offset)
    return ProjectList(items=[ProjectOut.model_validate(p) for p in items], total=total, limit=limit, offset=offset)


@router.get("/{project_id}", response_model=ProjectOut)
def read_project(project: Project = Depends(get_owned_project)) -> Project:
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(body: ProjectUpdate, project: Project = Depends(get_owned_project),
                   user: User = Depends(current_user), db: Session = Depends(get_db)) -> Project:
    return project_service.update(db, user, project.id, expected_version=body.expected_version,
                                  title=body.title, archived=body.archived)


@router.delete("/{project_id}", status_code=204)
def delete_project(project: Project = Depends(get_owned_project), user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> Response:
    project_service.delete(db, user, project.id)
    return Response(status_code=204)

from fastapi import APIRouter

from app.api.v1 import (
    appraisal,
    assist,
    auth,
    conversations,
    documents,
    extraction,
    jobs,
    prisma,
    projects,
    protocol,
    reports,
    screening,
    search,
    synthesis,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(conversations.router)
api_router.include_router(jobs.router)
api_router.include_router(protocol.router)
api_router.include_router(search.router)
api_router.include_router(screening.router)
api_router.include_router(documents.router)
api_router.include_router(prisma.router)
api_router.include_router(extraction.router)
api_router.include_router(appraisal.router)
api_router.include_router(synthesis.router)
api_router.include_router(reports.router)
api_router.include_router(assist.router)

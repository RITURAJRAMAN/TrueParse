from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from trueparse._version import get_version
from trueparse.api.batches import router as batches_router
from trueparse.api.errors import engine_error, http_error
from trueparse.api.jobs import router as jobs_router
from trueparse.api.lifecycle import lifespan
from trueparse.api.openapi import Application
from trueparse.api.routes import router
from trueparse.api.settings import cors_origins
from trueparse.exceptions import PDFEngineError
from trueparse.jobs.store import JobServiceError

app = Application(
    title="TrueParse",
    version=get_version(),
    description="Local PDF extraction with managed background jobs and batches.",
    lifespan=lifespan,
)
origins = cors_origins()
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key", "X-Filename", "X-Parse-Options", "Idempotency-Key"],
    )
app.exception_handler(PDFEngineError)(engine_error)
app.exception_handler(HTTPException)(http_error)
app.include_router(router)
app.include_router(jobs_router)
app.include_router(batches_router)


@app.exception_handler(JobServiceError)
async def job_error(_request, exc: JobServiceError):
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": str(exc)}})

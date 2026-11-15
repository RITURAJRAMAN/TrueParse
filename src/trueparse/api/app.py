from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware

from trueparse._version import get_version
from trueparse.api.errors import engine_error, http_error
from trueparse.api.openapi import Application
from trueparse.api.routes import router
from trueparse.api.settings import cors_origins
from trueparse.exceptions import PDFEngineError

app = Application(
    title="TrueParse",
    version=get_version(),
    description="Local PDF extraction. One request, requested outputs, no server-side artifacts.",
)
origins = cors_origins()
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key", "X-Filename", "X-Parse-Options"],
    )
app.exception_handler(PDFEngineError)(engine_error)
app.exception_handler(HTTPException)(http_error)
app.include_router(router)

import logging
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from models.request import OptimizationRequest
from models.response import OptimizationResponse
from services.groq_client import extract_directives
from services.optimizer import solve_energy_schedule
from core.config import settings

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("main")

# Initialize the FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Smart Campus Energy Optimization API with LLM Directive Interpretation and MILP Scheduling",
    version="1.0.0"
)

from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware

# Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Defense-in-depth security headers
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

# Custom Exception Handlers to comply with Challenge Rules
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return 400 for malformed or structurally invalid requests."""
    logger.warning(f"Request validation error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request structure or payload validation failed.", "errors": jsonable_encoder(exc.errors())}
    )

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Controlled 500 error response without exposing internal secrets or stack traces."""
    logger.error(f"Internal server error: {exc}", exc_info=False)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal error occurred while processing the energy schedule."}
    )

# Health Endpoint (Required by the judging system)
@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """Returns status ok when the service is ready."""
    return {"status": "ok"}

# Main Optimization Endpoint
@app.post("/optimize-energy", response_model=OptimizationResponse, status_code=status.HTTP_200_OK)
def optimize_energy(request: OptimizationRequest):
    """
    Main endpoint:
    1. Extracts structured directives from operator_notes using LLM with deterministic guardrails.
    2. Formulates and solves the MILP campus energy dispatching problem.
    3. Replays and validates constraints to return an optimal, verified 24-hour schedule.
    """
    logger.info(f"Processing scenario: {request.scenario_id} with {len(request.operator_notes)} notes")

    # Stage 1 & 2: Extract & validate directives
    directives = extract_directives(
        operator_notes=request.operator_notes,
        battery_capacity=request.battery.capacity_kwh
    )

    # Stage 3 & 4: Solve MILP formulation & verify schedule
    response = solve_energy_schedule(request=request, directives=directives)

    logger.info(f"Scenario {request.scenario_id} optimized: total_cost_bdt={response.total_cost_bdt}, peak_grid_kwh={response.peak_grid_kwh}")
    return response

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
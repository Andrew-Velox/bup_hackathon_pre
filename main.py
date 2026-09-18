from fastapi import FastAPI

# Initialize the FastAPI app
app = FastAPI(title="Campus Energy Optimization API")

# Health Endpoint (Required by the judging system)
@app.get("/health")
def health_check():
    """Returns status ok when the service is ready."""
    return {"status": "ok"}

# Placeholder for the main optimization endpoint
@app.post("/optimize-energy")
def optimize_energy():
    return {"message": "Optimization endpoint ready to be built"}
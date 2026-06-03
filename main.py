from fastapi import FastAPI
from pydantic import BaseModel
from typing import List

from utils import calculate_hydraulics

app = FastAPI()

class WellParameters(BaseModel):
    mud_density: float  # ppg
    flow_rate: float    # gpm
    drill_pipe_od: float # inches
    drill_pipe_id: float # inches
    annulus_od: float   # inches (hole size or casing ID)
    annulus_id: float   # inches (drill pipe OD or casing OD)
    temperature: float  # Fahrenheit
    depth: float        # feet

class FormationData(BaseModel):
    depth: float        # feet
    pore_pressure: float # ppg equivalent
    fracture_gradient: float # ppg equivalent

class HydraulicCalculationResult(BaseModel):
    friction_pressure_loss: float # psi
    ecd: float                    # ppg
    bhp: float                    # psi
    choke_pressure_requirement: float # psi

@app.post("/calculate_hydraulics", response_model=HydraulicCalculationResult)
async def run_hydraulic_calculations(params: WellParameters):
    results = calculate_hydraulics(
        mud_density=params.mud_density,
        flow_rate=params.flow_rate,
        drill_pipe_od=params.drill_pipe_od,
        drill_pipe_id=params.drill_pipe_id,
        annulus_od=params.annulus_od,
        annulus_id=params.annulus_id,
        temperature=params.temperature,
        depth=params.depth
    )
    return HydraulicCalculationResult(**results)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

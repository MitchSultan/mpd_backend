

import importlib
import os
import math
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING
from contextlib import asynccontextmanager

import numpy as np
from scipy.optimize import fsolve
import random
import asyncio
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

if TYPE_CHECKING:
    from supabase import Client  # type: ignore

try:
    _supabase = importlib.import_module("supabase")
    create_client = _supabase.create_client
    Client = _supabase.Client
except ImportError:
    create_client = None  # type: ignore
    Client = Any  # type: ignore

load_dotenv()

# ---------------------------------------------------------------------------
# Supabase client (singleton)
# ---------------------------------------------------------------------------

SUPABASE_URL = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
SUPABASE_KEY = os.environ["NEXT_PUBLIC_SUPABASE_SERVICE_KEY"]
supabase: Any = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------------------------------------------------------------
# App true
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("MPD Analytics Suite API starting...")
    yield
    print("Shutting down.")

app = FastAPI(title="MPD Analytics Suite", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.getenv("FRONTEND_URL", "http://localhost:3000"),
        "https://msproject-pied.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class FormationLayer(BaseModel):
    top_depth_ft: float
    bottom_depth_ft: float
    pore_pressure_ppg: float
    fracture_gradient_ppg: float
    lithology: Optional[str] = None
    geothermal_gradient: Optional[float] = None

class FormationDataInput(BaseModel):
    layers: list[FormationLayer]

class RealtimeSimulatorInput(BaseModel):
    flow_rate_gpm: float = 600
    mud_weight_ppg: float = 11.5
    bit_depth_ft: float = 12500
    duration_sec: int = 60
    interval_sec: int = 3
    simulation_type: str = "circulation"

class SimulationInput(BaseModel):
    well_id: str
    fluid_id: Optional[str] = None
    flow_rate_gpm: float
    bit_depth_ft: float
    mud_weight_ppg: float
    plastic_viscosity_cp: float
    yield_point_lbf100ft2: float
    drill_pipe_od_in: float
    drill_pipe_id_in: float
    hole_size_in: float
    surface_temperature_f: float = 75.0
    bottom_temperature_f: Optional[float] = None
    simulation_type: str = "static"


class MonitoringDataPoint(BaseModel):
    well_id: str
    pump_rate_gpm: Optional[float] = None
    standpipe_pressure_psi: Optional[float] = None
    rop_fthr: Optional[float] = None
    choke_opening_pct: Optional[float] = None
    surface_backpressure_psi: Optional[float] = None
    bhp_psi: Optional[float] = None
    ecd_ppg: Optional[float] = None
    mud_weight_ppg: Optional[float] = None
    flow_rate_in_gpm: Optional[float] = None
    flow_rate_out_gpm: Optional[float] = None
    pit_volume_bbl: Optional[float] = None
    gas_reading_units: Optional[float] = None


class ChokeInput(BaseModel):
    well_id: str
    target_bhp_psi: float
    mud_weight_ppg: float
    flow_rate_gpm: float
    bit_depth_ft: float
    surface_backpressure_psi: float


# ---------------------------------------------------------------------------
# Engineering calculation functions
# ---------------------------------------------------------------------------

def calculate_bhp(
    mud_weight_ppg: float,
    depth_ft: float,
    surface_backpressure_psi: float = 0.0,
) -> float:
    hydrostatic = mud_weight_ppg * 0.052 * depth_ft
    return hydrostatic + surface_backpressure_psi


def calculate_annular_friction_pressure(
    flow_rate_gpm: float,
    mud_weight_ppg: float,
    plastic_viscosity_cp: float,
    yield_point_lbf: float,
    hole_od_in: float,
    pipe_od_in: float,
    depth_ft: float,
) -> float:
    if flow_rate_gpm <= 0:
        return 0.0
    annular_gap = hole_od_in - pipe_od_in
    annular_area_in2 = math.pi / 4 * (hole_od_in**2 - pipe_od_in**2)
    annular_area_ft2 = annular_area_in2 / 144
    velocity_ftmin = flow_rate_gpm * 0.3208 / max(annular_area_ft2, 0.01)

    de = hole_od_in - pipe_od_in
    re_bingham = 109 * mud_weight_ppg * velocity_ftmin * de / max(plastic_viscosity_cp, 1)

    if re_bingham < 2100:
        f = 24 / re_bingham if re_bingham > 0 else 0
    else:
        f = 0.0791 / (re_bingham**0.25)

    velocity_fts = velocity_ftmin / 60
    friction_psi = (f * depth_ft * mud_weight_ppg * velocity_fts**2) / (25.81 * max(de, 0.1))
    return max(friction_psi, 0.0)


def calculate_swab_surge_pressure(
    velocity_ftmin: float,
    mud_weight_ppg: float,
    plastic_viscosity_cp: float,
    yield_point_lbf: float,
    hole_od_in: float,
    pipe_od_in: float,
    depth_ft: float,
) -> float:
    """Positive velocity is surge (running in), negative is swab (pulling out)."""
    if velocity_ftmin == 0:
        return 0.0
    
    pipe_area = math.pi / 4 * pipe_od_in**2
    annular_area = math.pi / 4 * (hole_od_in**2 - pipe_od_in**2)
    vm = velocity_ftmin * (pipe_area / max(annular_area, 0.1) + 1)
    
    de = hole_od_in - pipe_od_in
    re_bingham = 109 * mud_weight_ppg * abs(vm) * de / max(plastic_viscosity_cp, 1)
    
    if re_bingham < 2100:
        f = 24 / re_bingham if re_bingham > 0 else 0
    else:
        f = 0.0791 / (re_bingham**0.25)
        
    vm_fts = abs(vm) / 60
    pressure_loss = (f * depth_ft * mud_weight_ppg * vm_fts**2) / (25.81 * max(de, 0.1))
    return pressure_loss if velocity_ftmin > 0 else -pressure_loss


def calculate_ecd_equation(ecd_guess, mud_density, pressure_loss, depth):
    return ecd_guess - mud_density - (pressure_loss / (0.052 * max(depth, 1)))


def calculate_ecd(
    mud_weight_ppg: float,
    friction_loss_psi: float,
    depth_ft: float,
) -> float:
    if depth_ft <= 0:
        return mud_weight_ppg
    if friction_loss_psi == 0:
        return mud_weight_ppg
    
    ecd_initial_guess = mud_weight_ppg + 0.5
    ecd_solution = fsolve(calculate_ecd_equation, ecd_initial_guess, args=(mud_weight_ppg, friction_loss_psi, depth_ft))
    return float(ecd_solution[0])



def calculate_choke_pressure(
    target_bhp_psi: float,
    hydrostatic_psi: float,
    friction_psi: float,
) -> float:
    """
    Required surface backpressure (choke pressure) to maintain target BHP.
    choke_pressure = target_BHP - hydrostatic - friction
    """
    choke = target_bhp_psi - hydrostatic_psi - friction_psi
    return max(choke, 0.0)


def classify_alert(
    ecd_ppg: float,
    pore_pressure_ppg: float,
    fracture_gradient_ppg: float,
    flow_in: Optional[float],
    flow_out: Optional[float],
) -> str:
    """
    Traffic-light alert classification.
    """
    # Kick/loss check via flow discrepancy
    if flow_in is not None and flow_out is not None:
        discrepancy_pct = abs(flow_in - flow_out) / max(flow_in, 1) * 100
        if discrepancy_pct > 10:
            return "critical"

    # ECD vs operating window
    margin = fracture_gradient_ppg - ecd_ppg
    if margin < 0.1 or ecd_ppg < pore_pressure_ppg + 0.1:
        return "critical"
    if margin < 0.3:
        return "warning"

    return "normal"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/wells")
def list_wells():
    res = supabase.table("wells").select("*").order("created_at", desc=True).execute()
    return res.data


@app.get("/wells/{well_id}")
def get_well(well_id: str):
    res = supabase.table("wells").select("*").eq("id", well_id).single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Well not found")
    return res.data


@app.post("/simulate")
def run_simulation(payload: SimulationInput):
    """
    Run hydraulic calculations and persist result to hydraulic_simulations.
    Returns the saved row including computed outputs.
    """
    # Define pipe movement for tripping
    pipe_velocity_ftmin = 0.0
    if payload.simulation_type == "tripping_in":
        pipe_velocity_ftmin = 60.0
    elif payload.simulation_type == "tripping_out":
        pipe_velocity_ftmin = -60.0

    # Friction and swab/surge calculations
    friction = 0.0
    surge_swab = 0.0

    if payload.simulation_type in ["circulation", "dynamic", "static"]:
        friction = calculate_annular_friction_pressure(
            flow_rate_gpm=payload.flow_rate_gpm,
            mud_weight_ppg=payload.mud_weight_ppg,
            plastic_viscosity_cp=payload.plastic_viscosity_cp,
            yield_point_lbf=payload.yield_point_lbf100ft2,
            hole_od_in=payload.hole_size_in,
            pipe_od_in=payload.drill_pipe_od_in,
            depth_ft=payload.bit_depth_ft,
        )
    elif payload.simulation_type in ["tripping_in", "tripping_out", "tripping"]:
        surge_swab = calculate_swab_surge_pressure(
            velocity_ftmin=pipe_velocity_ftmin,
            mud_weight_ppg=payload.mud_weight_ppg,
            plastic_viscosity_cp=payload.plastic_viscosity_cp,
            yield_point_lbf=payload.yield_point_lbf100ft2,
            hole_od_in=payload.hole_size_in,
            pipe_od_in=payload.drill_pipe_od_in,
            depth_ft=payload.bit_depth_ft,
        )

    total_pressure_loss = friction + surge_swab

    choke_applied = 0.0
    
    bhp = calculate_bhp(payload.mud_weight_ppg, payload.bit_depth_ft, surface_backpressure_psi=choke_applied) + surge_swab
    
    ecd = calculate_ecd(payload.mud_weight_ppg, total_pressure_loss, payload.bit_depth_ft)

    # Annular velocity (ft/min)
    ann_area_in2 = math.pi / 4 * (payload.hole_size_in**2 - payload.drill_pipe_od_in**2)
    ann_area_ft2 = ann_area_in2 / 144
    ann_velocity = payload.flow_rate_gpm * 0.3208 / max(ann_area_ft2, 0.01)

    # Estimated bottom temperature (geothermal gradient ~1.5°F/100ft if not provided)
    bottom_temp = payload.bottom_temperature_f or (
        payload.surface_temperature_f + payload.bit_depth_ft * 0.015
    )

    # --- persist to Supabase ---
    row = {
        "well_id": payload.well_id,
        "fluid_id": payload.fluid_id,
        "flow_rate_gpm": payload.flow_rate_gpm,
        "bit_depth_ft": payload.bit_depth_ft,
        "surface_temperature_f": payload.surface_temperature_f,
        "bottom_temperature_f": round(bottom_temp, 2),
        "ecd_ppg": round(ecd, 3),
        "bhp_psi": round(bhp, 2),
        "friction_loss_psi": round(friction, 2),
        "choke_pressure_psi": None,     # populated separately by /choke endpoint
        "annular_velocity_ftmin": round(ann_velocity, 2),
        "simulation_type": payload.simulation_type,
    }

    result = supabase.table("hydraulic_simulations").insert(row).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save simulation")

    return result.data[0]


@app.post("/choke")
def calculate_choke(payload: ChokeInput):
    """
    Compute required choke pressure to hit a target BHP,
    then persist the choke setting.
    """
    hydrostatic = payload.mud_weight_ppg * 0.052 * payload.bit_depth_ft
    friction = calculate_annular_friction_pressure(
        flow_rate_gpm=payload.flow_rate_gpm,
        mud_weight_ppg=payload.mud_weight_ppg,
        plastic_viscosity_cp=20.0,      # default; real call should pass fluid props
        yield_point_lbf=15.0,
        hole_od_in=8.5,
        pipe_od_in=5.0,
        depth_ft=payload.bit_depth_ft,
    )
    choke_psi = calculate_choke_pressure(payload.target_bhp_psi, hydrostatic, friction)

    row = {
        "well_id": payload.well_id,
        "target_bhp_psi": payload.target_bhp_psi,
        "surface_backpressure_psi": choke_psi,
        "choke_opening_pct": payload.surface_backpressure_psi,
        "mud_weight_ppg": payload.mud_weight_ppg,
        "flow_rate_gpm": payload.flow_rate_gpm,
        "control_mode": "manual",
    }
    result = supabase.table("choke_settings").insert(row).execute()
    return {"choke_pressure_psi": round(choke_psi, 2), "saved": result.data[0]}


@app.post("/monitor")
def ingest_monitoring(payload: MonitoringDataPoint):
    """
    Receive a real-time sensor data point, classify alert status,
    then persist to realtime_monitoring.
    Called by your data acquisition layer / simulator.
    """
    # Fetch well's latest formation data for alert classification
    form = (
        supabase.table("formation_data")
        .select("pore_pressure_ppg, fracture_gradient_ppg")
        .eq("well_id", payload.well_id)
        .order("depth_ft", desc=True)
        .limit(1)
        .execute()
    )
    pore_p = form.data[0]["pore_pressure_ppg"] if form.data else 10.0
    frac_g = form.data[0]["fracture_gradient_ppg"] if form.data else 14.0

    alert = classify_alert(
        ecd_ppg=payload.ecd_ppg or 0,
        pore_pressure_ppg=pore_p,
        fracture_gradient_ppg=frac_g,
        flow_in=payload.flow_rate_in_gpm,
        flow_out=payload.flow_rate_out_gpm,
    )

    row = payload.model_dump()
    row["alert_status"] = alert
    result = supabase.table("realtime_monitoring").insert(row).execute()
    return result.data[0]


@app.get("/wells/{well_id}/latest")
def get_latest_monitoring(well_id: str):
    """
    Returns the most recent monitoring row for the dashboard KPI cards.
    """
    res = (
        supabase.table("realtime_monitoring")
        .select("*")
        .eq("well_id", well_id)
        .order("recorded_at", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return res.data[0]


@app.get("/wells/{well_id}/monitor/live")
def get_live_series(well_id: str, limit: int = 100):
    """
    Returns last N monitoring rows for time-series charts.
    """
    res = (
        supabase.table("realtime_monitoring")
        .select("recorded_at,bhp_psi,ecd_ppg,standpipe_pressure_psi,surface_backpressure_psi,pump_rate_gpm,alert_status")
        .eq("well_id", well_id)
        .order("recorded_at", desc=True)
        .limit(limit)
        .execute()
    )
    # Reverse so chart renders oldest → newest
    return list(reversed(res.data))


@app.get("/wells/{well_id}/window")
def get_operating_window(well_id: str):
    """
    Returns MPD operating window data for the pressure-vs-depth chart.
    Combines formation_data + latest simulations.
    """
    formation = (
        supabase.table("formation_data")
        .select("*")
        .eq("well_id", well_id)
        .order("depth_ft")
        .execute()
    )
    simulations = (
        supabase.table("hydraulic_simulations")
        .select("bit_depth_ft,ecd_ppg,bhp_psi")
        .eq("well_id", well_id)
        .order("simulated_at", desc=True)
        .limit(50)
        .execute()
    )
    return {
        "formation": formation.data,
        "simulations": simulations.data,
    }


@app.get("/wells/{well_id}/simulations")
def list_simulations(well_id: str, limit: int = 20):
    res = (
        supabase.table("hydraulic_simulations")
        .select("*")
        .eq("well_id", well_id)
        .order("simulated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


@app.post("/wells/{well_id}/formation")
def save_formation_data(well_id: str, payload: FormationDataInput):
    # clear existing data
    supabase.table("formation_data").delete().eq("well_id", well_id).execute()
    
    rows = []
    for layer in payload.layers:
        rows.append({
            "well_id": well_id,
            "depth_ft": layer.bottom_depth_ft,
            "pore_pressure_ppg": layer.pore_pressure_ppg,
            "fracture_gradient_ppg": layer.fracture_gradient_ppg,
            "lithology": layer.lithology
        })
    if rows:
        supabase.table("formation_data").insert(rows).execute()
    return {"status": "success", "inserted": len(rows)}

@app.get("/wells/{well_id}/formation")
def get_formation_data(well_id: str):
    res = supabase.table("formation_data").select("*").eq("well_id", well_id).order("depth_ft").execute()
    return res.data

@app.post("/wells/{well_id}/simulate-realtime")
def simulate_realtime(well_id: str, payload: RealtimeSimulatorInput, background_tasks: BackgroundTasks):
    def run_simulation_task():
        # Generate some quick data points right now to simulate history
        num_points = payload.duration_sec // payload.interval_sec
        rows = []
        
        for i in range(num_points):
            time_offset = (num_points - i) * payload.interval_sec
            
            # Add some noise
            flow = payload.flow_rate_gpm + random.uniform(-10, 10)
            choke = 450 + random.uniform(-5, 5)
            
            # Simple simulation logic inline
            hydrostatic = payload.mud_weight_ppg * 0.052 * payload.bit_depth_ft
            friction = calculate_annular_friction_pressure(
                flow_rate_gpm=flow,
                mud_weight_ppg=payload.mud_weight_ppg,
                plastic_viscosity_cp=15.0,
                yield_point_lbf=10.0,
                hole_od_in=8.5,
                pipe_od_in=5.0,
                depth_ft=payload.bit_depth_ft
            )
            bhp = hydrostatic + choke + friction
            ecd = calculate_ecd(payload.mud_weight_ppg, friction, payload.bit_depth_ft)
            
            rows.append({
                "well_id": well_id,
                "pump_rate_gpm": flow,
                "standpipe_pressure_psi": bhp - choke + friction, # SPP approx
                "choke_opening_pct": 30.0,
                "surface_backpressure_psi": choke,
                "bhp_psi": bhp,
                "ecd_ppg": ecd,
                "mud_weight_ppg": payload.mud_weight_ppg,
                "flow_rate_in_gpm": flow,
                "flow_rate_out_gpm": flow,
                "alert_status": "normal"
            })
            
        if rows:
            supabase.table("realtime_monitoring").insert(rows).execute()

    background_tasks.add_task(run_simulation_task)
    return {"status": "Simulator started"}
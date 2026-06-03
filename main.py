"""
MPD Analytics Suite — FastAPI backend (Railway)
Handles hydraulic calculations and persists all results to Supabase.




"""

import importlib
import os
import math
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, HTTPException
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
# App
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
    """
    BHP = (mud_weight × 0.052 × depth) + surface_backpressure
    0.052 is the conversion factor: ppg × ft → psi
    """
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
    """
    Bingham Plastic model — annular friction pressure loss (psi).
    Using API RP 13D simplified approach.
    """
    annular_gap = hole_od_in - pipe_od_in
    # Annular velocity (ft/min)
    annular_area_in2 = math.pi / 4 * (hole_od_in**2 - pipe_od_in**2)
    annular_area_ft2 = annular_area_in2 / 144
    velocity_ftmin = flow_rate_gpm * 0.3208 / annular_area_ft2

    # Equivalent diameter
    de = hole_od_in - pipe_od_in  # simplified

    # Bingham Reynolds number
    re = 109 * mud_weight_ppg * velocity_ftmin * de / plastic_viscosity_cp
    re_bingham = re  # simplified; full model uses Hedstrom number

    # Friction factor (Dodge-Metzner for Bingham)
    if re_bingham < 2100:
        f = 24 / re_bingham if re_bingham > 0 else 0
    else:
        f = 0.0791 / (re_bingham**0.25)

    # Friction pressure loss (psi)
    velocity_fts = velocity_ftmin / 60
    density_ppg = mud_weight_ppg
    friction_psi = (f * depth_ft * density_ppg * velocity_fts**2) / (
        25.81 * de
    )
    return max(friction_psi, 0.0)


def calculate_ecd(
    bhp_psi: float,
    friction_loss_psi: float,
    depth_ft: float,
) -> float:
    """
    ECD (ppg) = (BHP + annular friction) / (0.052 × depth)
    """
    if depth_ft <= 0:
        return 0.0
    return (bhp_psi + friction_loss_psi) / (0.052 * depth_ft)


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
    # --- calculations ---
    bhp = calculate_bhp(payload.mud_weight_ppg, payload.bit_depth_ft)

    friction = calculate_annular_friction_pressure(
        flow_rate_gpm=payload.flow_rate_gpm,
        mud_weight_ppg=payload.mud_weight_ppg,
        plastic_viscosity_cp=payload.plastic_viscosity_cp,
        yield_point_lbf=payload.yield_point_lbf100ft2,
        hole_od_in=payload.hole_size_in,
        pipe_od_in=payload.drill_pipe_od_in,
        depth_ft=payload.bit_depth_ft,
    )

    ecd = calculate_ecd(bhp, friction, payload.bit_depth_ft)

    # Annular velocity (ft/min)
    ann_area_in2 = math.pi / 4 * (payload.hole_size_in**2 - payload.drill_pipe_od_in**2)
    ann_area_ft2 = ann_area_in2 / 144
    ann_velocity = payload.flow_rate_gpm * 0.3208 / ann_area_ft2

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
        raise HTTPException(status_code=404, detail="No monitoring data found")
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
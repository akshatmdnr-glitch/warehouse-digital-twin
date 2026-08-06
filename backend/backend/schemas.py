"""Pydantic request/response schemas for the warehouse backend API."""

from typing import List, Optional

from pydantic import BaseModel, Field

# ── auth ──────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class UserOut(BaseModel):
    username: str
    role: str
    created_at: float
    last_login: Optional[float] = None
    active: bool = True


class TokenOut(BaseModel):
    token: str
    user: UserOut
    expires_at: float


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=4, max_length=256)
    role: str = Field(..., pattern="^(operator|supervisor|administrator)$")


# ── robots / tasks ────────────────────────────────────────────


class RobotIn(BaseModel):
    robot_id: str = Field(..., min_length=1, max_length=128)
    name: Optional[str] = None
    namespace: Optional[str] = ""
    robot_type: Optional[str] = "unknown"
    status: Optional[str] = "ONLINE"
    x: Optional[float] = None
    y: Optional[float] = None
    yaw: Optional[float] = None
    battery: Optional[float] = Field(None, ge=0, le=100)
    charging: Optional[bool] = False
    current_task: Optional[str] = ""
    payload_capacity: Optional[float] = Field(0, ge=0)
    max_speed: Optional[float] = Field(0, ge=0)
    workload: Optional[int] = Field(0, ge=0)
    priority: Optional[float] = Field(0, ge=0)


class RobotPatch(BaseModel):
    name: Optional[str] = None
    robot_type: Optional[str] = None
    status: Optional[str] = None
    battery: Optional[float] = Field(None, ge=0, le=100)
    charging: Optional[bool] = None
    current_task: Optional[str] = None
    payload_capacity: Optional[float] = Field(None, ge=0)
    max_speed: Optional[float] = Field(None, ge=0)
    workload: Optional[int] = Field(None, ge=0)
    priority: Optional[float] = Field(None, ge=0)


class TaskIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=128)
    pickup_x: float
    pickup_y: float
    dropoff_x: float
    dropoff_y: float
    priority: int = Field(1, ge=0, le=2)
    robot_id: Optional[str] = ""
    required_payload: float = Field(0, ge=0)


class TaskPatch(BaseModel):
    status: Optional[str] = None
    priority: Optional[int] = Field(None, ge=0, le=2)
    robot_id: Optional[str] = None
    pickup_x: Optional[float] = None
    pickup_y: Optional[float] = None
    dropoff_x: Optional[float] = None
    dropoff_y: Optional[float] = None
    required_payload: Optional[float] = Field(None, ge=0)


# ── maps / misc ───────────────────────────────────────────────


class MapIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    resolution: float = Field(..., gt=0)
    origin_x: float = 0
    origin_y: float = 0
    data: Optional[List[int]] = None


class IngestBatch(BaseModel):
    ts: Optional[float] = None
    robots: Optional[List[dict]] = []
    positions: Optional[List[dict]] = []
    batteries: Optional[List[dict]] = []
    tasks: Optional[List[dict]] = []
    fleet: Optional[dict] = None
    queue: Optional[List[dict]] = []
    reservations: Optional[List[dict]] = []
    events: Optional[List[dict]] = []
    alerts: Optional[List[dict]] = []
    analytics: Optional[dict] = None
    metrics: Optional[List[dict]] = []

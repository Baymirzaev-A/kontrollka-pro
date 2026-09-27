from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import datetime


class Device(BaseModel):
    ip: str = Field(..., pattern=r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')
    name: Optional[str] = None
    vendor: Optional[str] = None
    device_type: Optional[str] = None
    sys_descr: Optional[str] = None
    sys_object_id: Optional[str] = None
    community: Optional[str] = None
    location: Optional[str] = None
    contact: Optional[str] = None
    last_seen: Optional[datetime] = None
    snmp_version: str = "v2c"  # v1, v2c, v3
    community: Optional[str] = "public"
    snmp_v3_config: Optional[dict] = None


class DeviceResponse(Device):
    id: str


class ScanRequest(BaseModel):
    subnet: str = Field(..., pattern=r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$')
    community: str = "public"
    snmp_version: str = "v2c"

    @validator('snmp_version')
    def validate_snmp_version(cls, v):
        if v not in ['v1', 'v2c', 'v3']:
            raise ValueError('snmp_version must be v1, v2c, or v3')
        return v


class ScanStatus(BaseModel):
    task_id: str
    status: str
    devices_found: int = 0
    error: Optional[str] = None

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import discovery, devices
from app.core.db import close_neo4j, close_clickhouse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting UCMDB service")
    yield
    logger.info("Shutting down UCMDB service")
    await close_neo4j()
    await close_clickhouse()

app = FastAPI(
    title="Kontrollka UCMDB",
    description="Multi-vendor network discovery and CMDB",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(discovery.router, prefix="/api/discovery", tags=["discovery"])
app.include_router(devices.router, prefix="/api/devices", tags=["devices"])

@app.get("/health")
async def health():
    return {"status": "ok", "service": "daria"}
import os
import logging
from neo4j import AsyncGraphDatabase
from clickhouse_driver import Client as ClickHouseClient
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

neo4j_driver = None
clickhouse_client = None

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10))
async def init_neo4j():
    global neo4j_driver
    uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "changeme")
    neo4j_driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    await neo4j_driver.verify_connectivity()
    logger.info("Neo4j connected")
    return neo4j_driver

def get_neo4j_driver():
    global neo4j_driver
    if neo4j_driver is None:
        raise RuntimeError("Neo4j driver not initialized")
    return neo4j_driver

async def close_neo4j():
    global neo4j_driver
    if neo4j_driver:
        await neo4j_driver.close()
        logger.info("Neo4j disconnected")

def init_clickhouse():
    global clickhouse_client
    host = os.getenv("CLICKHOUSE_HOST", "clickhouse")
    port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    user = os.getenv("CLICKHOUSE_USER", "kontrollka")
    password = os.getenv("CLICKHOUSE_PASSWORD", "changeme")
    clickhouse_client = ClickHouseClient(
        host=host,
        port=port,
        user=user,
        password=password,
        database="kontrollka_metrics"
    )
    logger.info("ClickHouse connected")
    return clickhouse_client

def get_clickhouse_client():
    global clickhouse_client
    if clickhouse_client is None:
        raise RuntimeError("ClickHouse client not initialized")
    return clickhouse_client

async def close_clickhouse():
    global clickhouse_client
    if clickhouse_client:
        clickhouse_client.disconnect()
        logger.info("ClickHouse disconnected")
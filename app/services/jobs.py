"""Agent job lifecycle (§3 envelope -> §7 jobs API)."""
import json
import uuid

from app.db import audit
from app.schemas import AgentJobEnvelope


async def create_job(conn, tenant_id, envelope: AgentJobEnvelope, redis=None) -> dict:
    """Insert agent_jobs (idempotent by tenant+idempotency_key) and enqueue to Redis."""
    existing = await conn.fetchrow(
        "SELECT * FROM agent_jobs WHERE tenant_id = $1 AND idempotency_key = $2",
        tenant_id, envelope.idempotency_key)
    if existing:
        return {"duplicate": True, **dict(existing)}

    row = await conn.fetchrow(
        "INSERT INTO agent_jobs (tenant_id, agent, input_json, idempotency_key, state) "
        "VALUES ($1, $2, $3, $4, 'queued') RETURNING *",
        tenant_id, envelope.agent,
        json.dumps(envelope.model_dump(mode="json", exclude={"tenant_id", "job_id", "idempotency_key"})),
        envelope.idempotency_key)

    job_payload = {
        "tenant_id": str(tenant_id), "job_id": str(row["id"]), "agent": envelope.agent,
        "goal": envelope.goal, "input_refs": envelope.input_refs,
        "constraints": envelope.constraints.model_dump(), "approval_policy": envelope.approval_policy,
    }
    if redis is not None:
        await redis.rpush("apex:jobs", json.dumps(job_payload))
    await audit(conn, tenant_id, "system", "job.created", "agent_jobs", row["id"], {"agent": envelope.agent})
    return {"duplicate": False, "id": str(row["id"]), "state": "queued", "job": job_payload}


async def get_job(conn, tenant_id, job_id: uuid.UUID):
    return await conn.fetchrow("SELECT * FROM agent_jobs WHERE id = $1 AND tenant_id = $2",
                               job_id, tenant_id)


async def finish_job(conn, tenant_id, job_id: uuid.UUID, result, state: str, cost: float,
                     error: str | None = None) -> None:
    await conn.execute(
        "UPDATE agent_jobs SET state = $1, output_json = $2, cost_usd = $3, error = $4, finished_at = now() "
        "WHERE id = $5 AND tenant_id = $6",
        state, json.dumps(result.model_dump(mode="json")) if result is not None else None,
        cost, error, job_id, tenant_id)

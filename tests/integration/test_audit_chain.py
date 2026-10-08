from sqlalchemy import delete, update

from gsoi_assistant.api.container import Container
from gsoi_assistant.db import models


async def fill(c: Container, n: int = 4) -> None:
    for i in range(n):
        await c.audit.record(
            user_id="owner",
            actor="system",
            action="test.event",
            subject=f"s{i}",
            details={"i": i, "api_key": "sk-secretsecretsecret1"},
        )


async def test_chain_verifies(container: Container) -> None:
    assert (await container.audit.verify()).ok  # empty chain is fine
    await fill(container)
    res = await container.audit.verify()
    assert res.ok and res.entries == 4


async def test_secrets_are_redacted_in_audit_details(container: Container) -> None:
    await fill(container, 1)
    (entry,) = await container.audit._store.all()
    assert "sk-secretsecretsecret1" not in str(entry.details)


async def test_tampering_with_an_entry_is_detected(container: Container) -> None:
    await fill(container)
    async with container.repo._sf() as s, s.begin():
        await s.execute(
            update(models.AuditEntry).where(models.AuditEntry.seq == 2).values(subject="forged")
        )
    res = await container.audit.verify()
    assert not res.ok and res.first_bad_seq == 2


async def test_deleting_an_entry_is_detected(container: Container) -> None:
    await fill(container)
    async with container.repo._sf() as s, s.begin():
        await s.execute(delete(models.AuditEntry).where(models.AuditEntry.seq == 2))
    res = await container.audit.verify()
    assert not res.ok and res.first_bad_seq == 3

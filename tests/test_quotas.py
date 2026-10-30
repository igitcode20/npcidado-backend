from __future__ import annotations

from bson import ObjectId
import pytest

from app.bootstrap import utcnow
from app.config import Settings
from app.services.quotas import (
    QuotaExceeded,
    consume_quota,
    local_day,
    refund_quota,
    usage_for_user,
)

SETTINGS = Settings(
    secret_key="x" * 64,
    daily_message_limit=3,
    daily_document_limit=2,
    app_timezone="America/Managua",
)


def _user(database, role: str = "user"):
    user = {
        "_id": ObjectId(),
        "username": role,
        "username_key": role,
        "full_name": role,
        "role": role,
        "active": True,
        "created_at": utcnow(),
    }
    database.users.insert_one(user)
    return user


def test_regular_user_limits_and_refund(database) -> None:
    user = _user(database)
    for _ in range(3):
        consume_quota(database, user, "message", SETTINGS)
    usage = usage_for_user(database, user, SETTINGS)
    assert usage["messages"]["used"] == 3
    assert usage["messages"]["remaining"] == 0
    with pytest.raises(QuotaExceeded) as error:
        consume_quota(database, user, "message", SETTINGS)
    assert error.value.limit == 3

    refund_quota(database, user["_id"], "message", SETTINGS)
    usage = usage_for_user(database, user, SETTINGS)
    assert usage["messages"]["used"] == 2
    assert usage["messages"]["remaining"] == 1


def test_document_limit(database) -> None:
    user = _user(database)
    consume_quota(database, user, "document", SETTINGS)
    consume_quota(database, user, "document", SETTINGS)
    with pytest.raises(QuotaExceeded):
        consume_quota(database, user, "document", SETTINGS)


def test_admin_is_unlimited_but_counted(database) -> None:
    admin = _user(database, "admin")
    for _ in range(6):
        consume_quota(database, admin, "message", SETTINGS)
    usage = usage_for_user(database, admin, SETTINGS)
    assert usage["unlimited"] is True
    assert usage["messages"]["limit"] is None
    assert usage["messages"]["used"] == 6


def test_usage_row_is_unique_per_day(database) -> None:
    user = _user(database)
    consume_quota(database, user, "message", SETTINGS)
    row = database.usage.find_one({"user_id": user["_id"]})
    assert row["date"] == local_day(SETTINGS)
    assert database.usage.count_documents({"user_id": user["_id"]}) == 1

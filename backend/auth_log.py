"""Authentication audit log (user-auth-admin Req 6.4, 6.5, 19.19).

One JSON line per event with exactly three fields: event, outcome, and userId
(only for an existing User). Nothing else is ever written: no submitted loginId
of an unknown user, password, hash, salt, token, secret, grantId, purpose, or
draft title. Lambda ships stdout to CloudWatch Logs.
"""
import json


def log(event, outcome, user_id=None):
    """Write one JSON line {"event","outcome"[,"userId"]} to stdout."""
    entry = {"event": event, "outcome": outcome}
    if user_id:
        entry["userId"] = user_id
    print(json.dumps(entry, ensure_ascii=False), flush=True)

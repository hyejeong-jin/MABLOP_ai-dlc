"""Error shapes + HTTP status mapping (Req 5.1, 5.3, 5.4; design Error Handling)."""
import json

# code -> HTTP status (design Interfaces: common error shape).
STATUS = {
    "unauthorized": 401,  # token (5.1)
    "bad_request": 400,  # unknown/malformed action (8.1 dispatch)
    "rate_limited": 429,  # rate (5.3)
    "body_too_large": 413,  # body length (5.4)
    "bedrock_error": 502,  # Bedrock upstream
    "internal_error": 500,  # other
}


class MablopError(Exception):
    """Carries an error code the handler maps to a status + error body."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code

    @property
    def status(self):
        """HTTP status for this code (500 if unknown)."""
        return STATUS.get(self.code, 500)


def error_body(code, message=""):
    """Common error body: {"error": {"code","message"}}."""
    return {"error": {"code": code, "message": message or code}}


def response(status, body):
    """Lambda Function URL response: statusCode + JSON body + content-type."""
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def error_response(err):
    """Build an error response from a MablopError (or code string)."""
    if isinstance(err, MablopError):
        code, message = err.code, err.message
    else:
        code, message = "internal_error", str(err)
    return response(STATUS.get(code, 500), error_body(code, message))

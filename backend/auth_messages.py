"""Korean message constants for user-auth-admin (design D14).

All new backend messages live here as UTF-8 literals. Tests compare responses
against the literal strings of the requirements, not against these constants.
"""

# --- Registration / login (Req 1, 2) ---
REGISTER_ACCEPTED = "가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."
LOGIN_FAILED = "아이디 또는 비밀번호가 올바르지 않습니다."
ACCOUNT_PENDING = "승인 대기 중인 계정입니다."
ACCOUNT_DISABLED = "사용이 중지된 계정입니다."

# --- Authentication gate (Req 4, 6.6) ---
UNAUTHENTICATED = "로그인이 필요합니다. 다시 로그인하세요."
ADMIN_REQUIRED = "관리자 권한이 필요합니다."
INTERNAL = "일시적인 오류가 발생했습니다. 잠시 후 다시 시도하세요."

# --- Throttling (Req 5) ---
LOGIN_BLOCKED = "로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요."
REGISTER_RATE = "가입 신청이 너무 많습니다. 잠시 후 다시 시도하세요."
REGISTER_CLOSED = "현재 가입 신청을 받을 수 없습니다. 관리자에게 문의하세요."

# --- Input validation (Req 1.4, 3.4, 12.4) ---
INVALID_LOGIN_ID = "아이디는 영문 소문자, 숫자, 밑줄(_), 하이픈(-)으로 4~32자여야 합니다."
INVALID_NAME = "이름은 1~30자여야 합니다."
INVALID_PASSWORD = "비밀번호는 10~128자여야 합니다."
SAME_PASSWORD = "새 비밀번호는 현재 비밀번호와 달라야 합니다."
BAD_CURRENT = "현재 비밀번호가 올바르지 않습니다."

# --- Bootstrap (Req 9.3) ---
ADMIN_EXISTS = "이미 관리자 계정이 있습니다."
LOGIN_ID_TAKEN = "이미 사용 중인 아이디입니다."

# --- Administrator account actions (Req 11) ---
ADMIN_TARGET = "관리자 계정은 변경할 수 없습니다."
NOT_FOUND_TARGET = "대상 사용자를 찾을 수 없습니다."
NEED_PENDING_APPROVE = "승인 대기 상태의 계정만 승인할 수 있습니다."
NEED_PENDING_REJECT = "승인 대기 상태의 계정만 거절할 수 있습니다."
NEED_ACTIVE_DISABLE = "활성 상태의 계정만 비활성화할 수 있습니다."
NEED_DISABLED_ENABLE = "비활성 상태의 계정만 재활성화할 수 있습니다."

# --- Key / image path checks (Req 8.2, 8.7) ---
BAD_PATH = "허용되지 않는 경로입니다."
BAD_IMAGE_PATH = "허용되지 않는 이미지 경로입니다."

# --- Empty workspace (design, drafting engine) ---
NO_LEARNED_POSTS = "학습된 글이 없습니다. 먼저 스타일 학습을 진행하세요."

# --- Legacy binding (Req 9.17, 9.18) ---
LEGACY_ALREADY_BOUND = "레거시 데이터는 이미 다른 사용자에게 연결되어 있습니다."
LEGACY_SUCCEEDED = "기존 학습 데이터와 초안을 연결했습니다."
LEGACY_FAILED = (
    "기존 데이터 연결에 실패했습니다. 승인은 유지되었습니다. "
    "관리자 권한으로 데이터 연결을 다시 실행하세요."
)

# --- Bedrock usage Grants (Req 19, 20) ---
PURPOSE_TOO_LONG = "사용 목적은 100자 이하로 입력하세요."
GRANT_ALREADY_REQUESTED = "이미 승인 대기 중인 신청이 있습니다."
GRANT_LIMIT = "더 이상 신청할 수 없습니다. 관리자에게 문의하세요."
GRANT_EXPIRY_INVALID = "만료 기간은 7일, 30일, 90일 중에서 선택하세요."
GRANT_NOT_FOUND = "대상 권한을 찾을 수 없습니다."
GRANT_NEED_REQUESTED_APPROVE = "승인 대기 상태의 권한만 승인할 수 있습니다."
GRANT_NEED_REQUESTED_REJECT = "승인 대기 상태의 권한만 거절할 수 있습니다."
GRANT_NEED_APPROVED_REVOKE = "승인된 권한만 폐기할 수 있습니다."
GRANT_REQUIRED = "Bedrock 사용 권한이 필요합니다. 마이페이지에서 신청하세요."

# --- Draft history (Req 22) ---
DEFAULT_DRAFT_TITLE = "제목 없음"
BAD_DRAFT_ID = "올바르지 않은 초안 번호입니다."
DRAFT_NOT_FOUND = "대상 초안을 찾을 수 없습니다."

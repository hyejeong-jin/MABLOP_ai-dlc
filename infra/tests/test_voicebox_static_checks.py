"""Static infra checks for voice-box-content-research-branding (task 11.3).

Requirements 10.4, 13.2, 13.3. Read-only: parses files on disk, no AWS
credentials, no network, no IAM/resource changes.

Verification criteria (documented):
  A. template.yaml has no excluded services (API Gateway, DynamoDB, OpenSearch, ...).
  B. Every S3 bucket blocks all public access and declares SSE.
  C. No IAM statement (template role + lambda-role-policy.json) uses Action "*"
     or a service-wide wildcard such as "s3:*"; no Resource "*" for s3/bedrock.
  D. bedrock:* actions are granted only to the Lambda role.
  E. frontend/src never imports a Bedrock/S3 SDK or calls bedrock-runtime
     (the browser reaches AWS only through the Lambda Function URL).
  F. backend voc_store / research_store / activity_store never hardcode
     "users/" keys; per-user keys come from s3store.user_key().
"""
import re
from pathlib import Path

import pytest

from .test_iac_config import (
    REPO_ROOT,
    _as_list,
    _load_policy,
    _load_template,
)

BACKEND = REPO_ROOT / "backend"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
NEW_STORE_MODULES = ["voc_store.py", "research_store.py", "activity_store.py"]

EXCLUDED_TYPE_PREFIXES = (
    "AWS::ApiGateway::",
    "AWS::ApiGatewayV2::",
    "AWS::DynamoDB::",
    "AWS::OpenSearchService::",
    "AWS::OpenSearchServerless::",
    "AWS::Elasticsearch::",
    "AWS::Cognito::",
    "AWS::StepFunctions::",
    "AWS::RDS::",
    "AWS::EC2::Instance",
    "AWS::ECS::",
    "AWS::EKS::",
    "AWS::Kendra::",
    "AWS::Bedrock::KnowledgeBase",
    "AWS::Bedrock::Agent",
)
SERVERLESS_EXCLUDED = {
    "AWS::Serverless::Api",
    "AWS::Serverless::HttpApi",
    "AWS::Serverless::SimpleTable",
    "AWS::Serverless::StateMachine",
}


def _resources():
    return _load_template().get("Resources", {})


def _all_statements():
    """Yield (source, statement) for the template role and the policy JSON."""
    for name, res in _resources().items():
        if res.get("Type") != "AWS::IAM::Role":
            continue
        for pol in _as_list(res.get("Properties", {}).get("Policies")):
            for st in _as_list(pol["PolicyDocument"].get("Statement")):
                yield f"template:{name}", st
    for st in _as_list(_load_policy().get("Statement")):
        yield "lambda-role-policy.json", st


def test_template_has_no_excluded_services():
    bad = []
    for name, res in _resources().items():
        typ = res.get("Type", "")
        if typ.startswith(EXCLUDED_TYPE_PREFIXES) or typ in SERVERLESS_EXCLUDED:
            bad.append((name, typ))
        # Function events must not create an API Gateway.
        events = res.get("Properties", {}).get("Events", {}) or {}
        for ev_name, ev in events.items():
            if ev.get("Type") in ("Api", "HttpApi"):
                bad.append((name, f"event {ev_name}: {ev.get('Type')}"))
    assert not bad, f"excluded services in template: {bad}"


def test_every_bucket_blocks_public_access_and_has_sse():
    buckets = {n: r for n, r in _resources().items() if r.get("Type") == "AWS::S3::Bucket"}
    assert buckets, "expected at least one S3 bucket"
    for name, res in buckets.items():
        props = res.get("Properties", {})
        pab = props.get("PublicAccessBlockConfiguration", {})
        for flag in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"):
            assert pab.get(flag) is True, f"{name}.{flag} must be true"
        rules = props.get("BucketEncryption", {}).get("ServerSideEncryptionConfiguration", [])
        assert rules, f"{name} missing SSE configuration"
        for rule in rules:
            algo = rule["ServerSideEncryptionByDefault"]["SSEAlgorithm"]
            assert algo in ("AES256", "aws:kms"), f"{name} bad SSE algorithm {algo}"


def test_no_bucket_policy_grants_public_principal():
    for name, res in _resources().items():
        if res.get("Type") != "AWS::S3::BucketPolicy":
            continue
        for st in _as_list(res["Properties"]["PolicyDocument"].get("Statement")):
            if st.get("Effect") == "Allow":
                assert st.get("Principal") not in ("*", {"AWS": "*"}), f"{name} allows public principal"


def test_no_wildcard_actions_in_any_role_policy():
    for source, st in _all_statements():
        if st.get("Effect") != "Allow":
            continue
        for action in _as_list(st.get("Action")):
            assert action != "*", f"{source}/{st.get('Sid')} uses Action '*'"
            assert not action.endswith(":*"), f"{source}/{st.get('Sid')} uses service wildcard {action}"


def test_no_bare_wildcard_resource_for_s3_or_bedrock():
    for source, st in _all_statements():
        actions = _as_list(st.get("Action"))
        if not any(a.startswith(("s3:", "bedrock:")) for a in actions):
            continue
        for res in _as_list(st.get("Resource")):
            assert res != "*", f"{source}/{st.get('Sid')} has Resource '*'"


def test_bedrock_permissions_only_on_lambda_role():
    lambda_role_ref = None
    for name, res in _resources().items():
        if res.get("Type") == "AWS::Serverless::Function":
            role = res["Properties"].get("Role", {})
            lambda_role_ref = role.get("!GetAtt") or role.get("GetAtt")
    assert lambda_role_ref, "function must use an explicit role"
    lambda_role_name = (lambda_role_ref if isinstance(lambda_role_ref, str) else lambda_role_ref[0]).split(".")[0]
    for source, st in _all_statements():
        if any(a.startswith("bedrock:") for a in _as_list(st.get("Action"))):
            assert source in (f"template:{lambda_role_name}", "lambda-role-policy.json"), (
                f"bedrock permission outside Lambda role: {source}"
            )
    # No other resource type may carry inline bedrock actions (e.g. extra roles/policies).
    for name, res in _resources().items():
        if res.get("Type") in ("AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"):
            doc = res["Properties"]["PolicyDocument"]
            for st in _as_list(doc.get("Statement")):
                assert not any(a.startswith("bedrock:") for a in _as_list(st.get("Action"))), name


_FORBIDDEN_FRONTEND = [
    re.compile(r"@aws-sdk/client-bedrock"),
    re.compile(r"@aws-sdk/client-s3"),
    re.compile(r"@aws-sdk/s3-"),
    re.compile(r"aws-sdk\b"),
    re.compile(r"bedrock-runtime", re.I),
    re.compile(r"\bInvokeModel\w*"),
    re.compile(r"\.s3\.[a-z0-9-]*\.?amazonaws\.com", re.I),
]


def _frontend_files():
    exts = {".js", ".jsx", ".ts", ".tsx", ".mjs"}
    # *.test.* files are excluded: they legitimately mention SDK names in guard assertions.
    return [
        p for p in FRONTEND_SRC.rglob("*")
        if p.is_file() and p.suffix in exts and ".test." not in p.name
    ]


def test_frontend_never_calls_bedrock_or_s3_directly():
    files = _frontend_files()
    assert files, "frontend/src has no source files"
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in _FORBIDDEN_FRONTEND:
            if pat.search(text):
                hits.append((str(path.relative_to(REPO_ROOT)), pat.pattern))
    assert not hits, f"direct AWS SDK/endpoint use in frontend: {hits}"


def test_forbidden_frontend_patterns_detect_violations():
    """Fixture: the scanner must flag representative violations."""
    samples = [
        "import { BedrockRuntimeClient } from '@aws-sdk/client-bedrock-runtime'",
        "fetch('https://bedrock-runtime.us-east-1.amazonaws.com/model/x/invoke')",
        "import { S3Client } from '@aws-sdk/client-s3'",
        "const c = new InvokeModelCommand({})",
    ]
    for s in samples:
        assert any(p.search(s) for p in _FORBIDDEN_FRONTEND), s


_HARDCODED_USERS_KEY = re.compile(r"""["']users/|f["']users/|["']/users/""")


@pytest.mark.parametrize("module", NEW_STORE_MODULES)
def test_new_store_modules_do_not_hardcode_users_prefix(module):
    path = BACKEND / module
    assert path.exists(), module
    text = path.read_text(encoding="utf-8")
    code_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    offenders = [ln.strip() for ln in code_lines if _HARDCODED_USERS_KEY.search(ln)]
    assert not offenders, f"{module} hardcodes users/ keys: {offenders}"
    assert "s3store.user_key(" in text, f"{module} must build keys via s3store.user_key()"


def test_hardcoded_users_key_pattern_detects_violations():
    """Fixture: the pattern flags hardcoded keys but not user_key() calls."""
    assert _HARDCODED_USERS_KEY.search('key = f"users/{uid}/voc.json"')
    assert _HARDCODED_USERS_KEY.search("key = 'users/abc/x.json'")
    assert not _HARDCODED_USERS_KEY.search('key = s3store.user_key("voc/index.json")')

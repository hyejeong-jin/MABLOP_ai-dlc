"""IaC / config snapshot verification checks for MABLOP MVP.

These are NOT property-based tests. They are architectural snapshot / smoke
checks (per task 14.5) that parse the IaC and config artifacts directly and
assert the locked architecture constraints hold. They require NO AWS
credentials and make no network calls -- everything is read off disk.

Covered constraints (Requirements 6.3, 6.4, 7.6, 8.1, 8.5, 9.1, 9.2, 9.3,
10.1, 10.2; Design: Testing Strategy -> IaC / config checks):

  1. IAM least-privilege (lambda-role-policy.json)
  2. Bucket private + SSE (template.yaml)
  3. Region us-east-1 (workflows + ARNs)
  4. Billing alarm present (template.yaml)
  5. GitHub Actions OIDC, no static keys (workflows)
  6. Excluded services absent from template.yaml
  7. Required AIDLC docs present and non-empty
"""
import json
import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths (repo root = two levels up from this file: infra/tests -> infra -> root)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA = REPO_ROOT / "infra"
POLICY_PATH = INFRA / "lambda-role-policy.json"
TEMPLATE_PATH = INFRA / "template.yaml"
WF_BACKEND = REPO_ROOT / ".github" / "workflows" / "deploy-backend.yml"
WF_FRONTEND = REPO_ROOT / ".github" / "workflows" / "deploy-frontend.yml"
AIDLC = REPO_ROOT / "docs" / "aidlc"

# Eight required S3 prefixes (Design: Data Model).
REQUIRED_PREFIXES = {
    "raw-posts",
    "embeddings",
    "vector-index",
    "style-profile",
    "images",
    "generated-posts",
    "prompt-templates",
    "metadata",
}


# ---------------------------------------------------------------------------
# CloudFormation-tolerant YAML loader: register short tags (!Ref/!GetAtt/!Sub
# etc.) as a multi-constructor so SafeLoader does not choke on them.
# ---------------------------------------------------------------------------
class _CfnLoader(yaml.SafeLoader):
    pass


def _cfn_multi(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {tag_suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_suffix: loader.construct_sequence(node)}
    return {tag_suffix: loader.construct_mapping(node)}


_CfnLoader.add_multi_constructor("!", _cfn_multi)


def _load_template():
    with TEMPLATE_PATH.open(encoding="utf-8") as fh:
        return yaml.load(fh, Loader=_CfnLoader)


def _load_policy():
    with POLICY_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _as_list(x):
    """Normalize a scalar-or-list CloudFormation/IAM field to a list."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


# ---------------------------------------------------------------------------
# 1. IAM least-privilege
# ---------------------------------------------------------------------------
def test_iam_no_wildcard_action_or_resource():
    """Req 6.3: no statement grants '*' action or account-wide '*' resource."""
    policy = _load_policy()
    for stmt in policy["Statement"]:
        actions = _as_list(stmt.get("Action"))
        assert "*" not in actions, f"{stmt.get('Sid')}: wildcard '*' action"
        resources = _as_list(stmt.get("Resource"))
        assert "*" not in resources, f"{stmt.get('Sid')}: wildcard '*' resource"


def test_iam_bedrock_action_is_exactly_invoke_model():
    """Req 6.3: bedrock action is exactly bedrock:InvokeModel, never bedrock:*."""
    policy = _load_policy()
    bedrock_stmts = [
        s
        for s in policy["Statement"]
        if any(a.startswith("bedrock:") for a in _as_list(s.get("Action")))
    ]
    assert bedrock_stmts, "no Bedrock statement found"
    for stmt in bedrock_stmts:
        actions = _as_list(stmt["Action"])
        assert actions == ["bedrock:InvokeModel"], (
            f"{stmt.get('Sid')}: bedrock actions must be exactly "
            f"['bedrock:InvokeModel'], got {actions}"
        )
        assert "bedrock:*" not in actions


def test_iam_s3_actions_only_get_put_scoped_to_prefixes():
    """Req 6.3: S3 is only Get/PutObject scoped to the 8 prefixes.

    No bucket-wide 's3:*', no 'Resource':'*', every resource is a
    prefix-scoped ARN, and all 8 required prefixes are covered.
    """
    policy = _load_policy()
    s3_stmts = [
        s
        for s in policy["Statement"]
        if any(a.startswith("s3:") for a in _as_list(s.get("Action")))
    ]
    assert s3_stmts, "no S3 statement found"
    covered = set()
    for stmt in s3_stmts:
        actions = set(_as_list(stmt["Action"]))
        assert actions == {"s3:GetObject", "s3:PutObject"}, (
            f"{stmt.get('Sid')}: S3 actions must be exactly Get/PutObject, "
            f"got {sorted(actions)}"
        )
        assert "s3:*" not in actions
        for res in _as_list(stmt.get("Resource")):
            assert res != "*", f"{stmt.get('Sid')}: bucket-wide S3 resource"
            # arn:aws:s3:::<bucket>/<prefix>/* -> capture <prefix>
            m = re.match(r"^arn:aws:s3:::[^/]+/([^/]+)/\*$", res)
            assert m, f"{stmt.get('Sid')}: S3 resource not prefix-scoped: {res}"
            covered.add(m.group(1))
    assert covered == REQUIRED_PREFIXES, (
        f"S3 prefixes must be exactly the 8 required, got {sorted(covered)}"
    )


def test_iam_logs_scoped_to_lambda_log_group():
    """Req 6.3: logs resource is the Lambda log group, not account-wide '*'."""
    policy = _load_policy()
    logs_stmts = [
        s
        for s in policy["Statement"]
        if any(a.startswith("logs:") for a in _as_list(s.get("Action")))
    ]
    assert logs_stmts, "no logs statement found"
    for stmt in logs_stmts:
        for res in _as_list(stmt.get("Resource")):
            assert res != "*", f"{stmt.get('Sid')}: account-wide logs resource"
            assert res.startswith("arn:aws:logs:") and "log-group:/aws/lambda/" in res, (
                f"{stmt.get('Sid')}: logs resource not scoped to a Lambda "
                f"log group: {res}"
            )


# ---------------------------------------------------------------------------
# 2. Bucket private + SSE
# ---------------------------------------------------------------------------
def _s3_bucket_resources(template):
    return [
        r
        for r in template["Resources"].values()
        if r.get("Type") == "AWS::S3::Bucket"
    ]


def test_bucket_public_access_fully_blocked():
    """Req 6.4 / 8.2: all four PublicAccessBlock flags are true."""
    buckets = _s3_bucket_resources(_load_template())
    assert buckets, "no AWS::S3::Bucket resource found"
    for b in buckets:
        pab = b["Properties"]["PublicAccessBlockConfiguration"]
        for flag in (
            "BlockPublicAcls",
            "BlockPublicPolicy",
            "IgnorePublicAcls",
            "RestrictPublicBuckets",
        ):
            assert pab.get(flag) is True, f"PublicAccessBlock {flag} must be true"


def test_bucket_sse_enabled():
    """Req 6.4: bucket has server-side encryption configured."""
    buckets = _s3_bucket_resources(_load_template())
    assert buckets, "no AWS::S3::Bucket resource found"
    for b in buckets:
        enc = b["Properties"]["BucketEncryption"]
        rules = enc["ServerSideEncryptionConfiguration"]
        assert rules, "BucketEncryption has no rules"
        for rule in rules:
            algo = rule["ServerSideEncryptionByDefault"]["SSEAlgorithm"]
            assert algo in ("AES256", "aws:kms"), f"unexpected SSE algorithm {algo}"


# ---------------------------------------------------------------------------
# 3. Region us-east-1
# ---------------------------------------------------------------------------
def test_backend_workflow_region_is_us_east_1_only():
    """Req 9.x: backend deploy references us-east-1 and no other deploy region."""
    text = WF_BACKEND.read_text(encoding="utf-8")
    assert "us-east-1" in text, "backend workflow does not reference us-east-1"
    others = {
        r
        for r in re.findall(r"\b(?:us|eu|ap|sa|ca|me|af)-[a-z]+-\d\b", text)
        if r != "us-east-1"
    }
    assert not others, f"unexpected non us-east-1 regions in backend workflow: {others}"


def test_iam_policy_arns_region_is_us_east_1():
    """Req 9.x: regional ARNs in the role policy reference us-east-1 only."""
    text = POLICY_PATH.read_text(encoding="utf-8")
    others = {
        r
        for r in re.findall(r"\b(?:us|eu|ap|sa|ca|me|af)-[a-z]+-\d\b", text)
        if r != "us-east-1"
    }
    assert not others, f"unexpected non us-east-1 regions in IAM policy: {others}"


# ---------------------------------------------------------------------------
# 4. Billing alarm present
# ---------------------------------------------------------------------------
def test_billing_alarm_present():
    """Req 7.6: a CloudWatch alarm on AWS/Billing EstimatedCharges exists."""
    template = _load_template()
    alarms = [
        r
        for r in template["Resources"].values()
        if r.get("Type") == "AWS::CloudWatch::Alarm"
    ]
    billing = [
        a
        for a in alarms
        if a["Properties"].get("Namespace") == "AWS/Billing"
        and a["Properties"].get("MetricName") == "EstimatedCharges"
    ]
    assert billing, "no AWS/Billing EstimatedCharges CloudWatch alarm found"


# ---------------------------------------------------------------------------
# 5. GitHub Actions OIDC, no static keys
# ---------------------------------------------------------------------------
def test_backend_workflow_uses_oidc_role():
    """Req 9.1/9.2: id-token: write permission + role-to-assume (OIDC federation)."""
    wf = yaml.safe_load(WF_BACKEND.read_text(encoding="utf-8"))
    perms = wf.get("permissions") or {}
    assert perms.get("id-token") == "write", "backend workflow missing id-token: write"
    text = WF_BACKEND.read_text(encoding="utf-8")
    assert "role-to-assume" in text, "backend workflow does not use role-to-assume (OIDC)"


def test_workflows_contain_no_static_aws_keys():
    """Req 9.3: no static access keys / secrets in either deploy workflow."""
    forbidden = ("aws-access-key-id", "aws-secret-access-key")
    for wf in (WF_BACKEND, WF_FRONTEND):
        text = wf.read_text(encoding="utf-8")
        lower = text.lower()
        for token in forbidden:
            assert token not in lower, f"{wf.name}: contains static key field '{token}'"
        assert not re.search(r"\bAKIA[0-9A-Z]{16}\b", text), (
            f"{wf.name}: contains a hardcoded AKIA access key id"
        )


# ---------------------------------------------------------------------------
# 6. Excluded services absent from template.yaml
# ---------------------------------------------------------------------------
# Prefixes/patterns of resource Type values that MUST NOT appear (Req 8.5).
EXCLUDED_TYPE_PATTERNS = (
    r"AWS::ApiGateway",            # API Gateway (v1 + v2)
    r"AWS::DynamoDB",              # DynamoDB
    r"AWS::Cognito",               # Cognito
    r"AWS::StepFunctions",         # Step Functions
    r"AWS::States",                # Step Functions (StateMachine type)
    r"AWS::OpenSearch",            # OpenSearch
    r"AWS::Elasticsearch",         # Elasticsearch
    r"AWS::RDS",                   # RDS
    r"AWS::EC2::Instance",         # EC2 instances
    r"AWS::ECS",                   # ECS / Fargate
    r"AWS::EKS",                   # EKS / Kubernetes
)


def test_excluded_services_absent_from_template():
    """Req 8.5: no excluded service resource types appear in the template.

    Matched against structured resource 'Type:' values, not comments.
    """
    template = _load_template()
    types = {r.get("Type") for r in template["Resources"].values()}
    for t in types:
        for pat in EXCLUDED_TYPE_PATTERNS:
            assert not re.match(pat, t or ""), (
                f"excluded service resource type present: {t} (matched {pat})"
            )


# ---------------------------------------------------------------------------
# 7. Required AIDLC docs present and non-empty
# ---------------------------------------------------------------------------
REQUIRED_AIDLC_DOCS = [
    AIDLC / "inception" / "project-context.md",
    AIDLC / "inception" / "requirements.md",
    AIDLC / "inception" / "architecture.md",
    AIDLC / "inception" / "decisions.md",
    AIDLC / "inception" / "cost-model.md",
    AIDLC / "inception" / "security.md",
    AIDLC / "inception" / "roadmap.md",
    AIDLC / "state.md",
    AIDLC / "backlog.md",
]


def test_required_aidlc_docs_present_and_non_empty():
    """Req 10.1/10.2: the 7 inception docs + state.md + backlog.md exist, non-empty."""
    for doc in REQUIRED_AIDLC_DOCS:
        assert doc.is_file(), f"missing required AIDLC doc: {doc.relative_to(REPO_ROOT)}"
        assert doc.stat().st_size > 0, f"empty AIDLC doc: {doc.relative_to(REPO_ROOT)}"


# ---------------------------------------------------------------------------
# 8. Template inline execution-role policy is wired AND least-privilege.
#    (The deployed role comes from template.yaml, not the standalone JSON doc;
#     these guard against the role being empty or over-broad in the template.)
# ---------------------------------------------------------------------------
def _lambda_role(template):
    for r in template["Resources"].values():
        if r.get("Type") == "AWS::IAM::Role":
            return r
    raise AssertionError("no AWS::IAM::Role in template")


def test_template_role_has_nonempty_inline_policy():
    """Req 6.3: the Lambda role in the template actually carries a policy."""
    role = _lambda_role(_load_template())
    policies = role["Properties"].get("Policies") or []
    assert policies, "MablopLambdaRole has no inline Policies (would deny all at runtime)"
    stmts = policies[0]["PolicyDocument"]["Statement"]
    actions = {a for s in stmts for a in _as_list(s.get("Action"))}
    assert "bedrock:InvokeModel" in actions
    assert {"s3:GetObject", "s3:PutObject"} <= actions
    assert "bedrock:*" not in actions and "s3:*" not in actions and "*" not in actions


def test_template_role_logs_and_s3_scoped():
    """Req 6.3: template inline policy has no wildcard resources."""
    role = _lambda_role(_load_template())
    stmts = role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    for s in stmts:
        for res in _as_list(s.get("Resource")):
            # Sub-rendered ARNs are dicts ({'Sub': ...}) under our loader; strings must not be "*".
            if isinstance(res, str):
                assert res != "*", f"{s.get('Sid')}: wildcard resource"


def test_function_url_invoke_permission_present():
    """Req 8.1: AuthType NONE Function URL needs an explicit public invoke permission."""
    template = _load_template()
    perms = [
        r for r in template["Resources"].values()
        if r.get("Type") == "AWS::Lambda::Permission"
        and r["Properties"].get("Action") == "lambda:InvokeFunctionUrl"
    ]
    assert perms, "no lambda:InvokeFunctionUrl permission for the Function URL"
    assert perms[0]["Properties"].get("FunctionUrlAuthType") == "NONE"


def test_log_group_present():
    """Logs scope must resolve to a real log group resource."""
    template = _load_template()
    lgs = [r for r in template["Resources"].values() if r.get("Type") == "AWS::Logs::LogGroup"]
    assert lgs, "no AWS::Logs::LogGroup resource"

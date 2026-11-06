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
  8. user-auth-admin (Req 7.1, 7.4, 18.1, 18.4, 18.5, 18.8, 18.10, 18.11):
     per-Sid S3/SSM statements, delete limited to three patterns, CORS
     unchanged, signing-secret path wiring, no secret values, resource types.
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


# Statement-level helpers shared by the policy JSON and the template role.
LIST_PREFIX_VALUES = {"raw-posts/*", "users/*", "generated-posts/*"}
DELETE_PATTERNS = {
    "users/_auth/login/*",
    "users/u_*",
}
_BUCKET = "arn:aws:s3:::${Bucket}"


def _norm_resource(res):
    """Canonicalize a resource so the template role and the policy JSON compare equal.

    ``{"Sub": "..."}`` (template) is unwrapped; the bucket and account
    placeholders of both documents map to ``${Bucket}`` / ``${AccountId}``.
    """
    if isinstance(res, dict):
        assert set(res) == {"Sub"}, f"unexpected intrinsic in resource: {res}"
        res = res["Sub"]
    assert isinstance(res, str), f"unexpected resource type: {res!r}"
    return (
        res.replace("${MablopBucket}", "${Bucket}")
        .replace("${BucketName}", "${Bucket}")
        .replace("${AWS::AccountId}", "${AccountId}")
    )


_norm_policy_resource = _norm_resource
_norm_template_resource = _norm_resource


def _stmt_by_sid(statements, sid):
    found = [s for s in statements if s.get("Sid") == sid]
    assert len(found) == 1, f"expected exactly one statement with Sid {sid}, got {len(found)}"
    return found[0]


def _s3_statements(statements):
    return [
        s for s in statements if any(a.startswith("s3:") for a in _as_list(s.get("Action")))
    ]


def _s3_prefix_names(stmt):
    """arn:aws:s3:::<bucket>/<prefix>/* -> {<prefix>} (asserts each is prefix-scoped)."""
    names = set()
    for res in _as_list(stmt["Resource"]):
        norm = _norm_resource(res)
        m = re.match(r"^arn:aws:s3:::\$\{Bucket\}/([^/]+)/\*$", norm)
        assert m, f"{stmt.get('Sid')}: S3 resource not prefix-scoped: {norm}"
        names.add(m.group(1))
    return names


def _bucket_level_resource(stmt):
    resources = [_norm_resource(r) for r in _as_list(stmt["Resource"])]
    return resources == [_BUCKET]


def _list_prefix_values(stmt):
    return _as_list(stmt["Condition"]["StringLike"]["s3:prefix"])


def _assert_delete_statement(statements, norm):
    stmt = _stmt_by_sid(statements, "DeleteUserObjects")
    assert stmt.get("Effect") == "Allow"
    assert _as_list(stmt["Action"]) == ["s3:DeleteObject"]
    resources = [norm(r) for r in _as_list(stmt["Resource"])]
    assert len(resources) == len(set(resources)) == 2
    assert set(resources) == {f"{_BUCKET}/{p}" for p in DELETE_PATTERNS}
    assert "Condition" not in stmt
    # Only the dedicated statement may grant delete.
    for s in statements:
        if s is stmt:
            continue
        for a in _as_list(s.get("Action")):
            assert "Delete" not in a and a not in ("s3:*", "*"), (
                f"{s.get('Sid')}: unexpected delete-capable action {a}"
            )


def _assert_delete_matching(patterns):
    """IAM '*' spans any characters, like fnmatch; check what can / cannot be deleted."""
    import fnmatch

    def deletable(key):
        arn = f"arn:aws:s3:::b/{key}"
        return any(
            fnmatch.fnmatchcase(arn, p.replace("${Bucket}", "b")) for p in patterns
        )

    allowed = [
        "users/_auth/login/jhe8888.json",
        "users/u_0123456789abcdef/account.json",
        "users/u_0123456789abcdef/generated-posts/post-1.md",
        "users/u_0123456789abcdef/metadata/grants.json",
    ]
    for key in allowed:
        assert deletable(key), f"should be deletable: {key}"

    legacy_keys = [f"{p}/x" for p in sorted(REQUIRED_PREFIXES)] + [
        "raw-posts/a.txt",
        "generated-posts/a.md",
        "metadata/ratelimit.json",
        "metadata/learning-status.json",
        "style-profile/profile.json",
        "vector-index/index.json",
    ]
    other_user_keys = [
        "users/_auth/legacy-owner.json",
        "users/_auth/admin.json",
    ]
    for key in legacy_keys + other_user_keys:
        assert not deletable(key), f"must NOT be deletable: {key}"


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
    """Req 6.3 / 18.4: S3 Get/Put is limited to the 8 legacy prefixes + users.

    The ``S3Prefixes`` statement is exactly Get/PutObject on 9 prefix-scoped
    ARNs (legacy 8 + ``users``). No bucket-wide 's3:*' and no 'Resource':'*'
    on any S3 statement.
    """
    policy = _load_policy()
    s3_stmts = _s3_statements(policy["Statement"])
    assert s3_stmts, "no S3 statement found"
    for stmt in s3_stmts:
        actions = set(_as_list(stmt["Action"]))
        assert "s3:*" not in actions
        for res in _as_list(stmt.get("Resource")):
            assert res != "*", f"{stmt.get('Sid')}: bucket-wide S3 resource"

    stmt = _stmt_by_sid(policy["Statement"], "S3Prefixes")
    assert set(_as_list(stmt["Action"])) == {"s3:GetObject", "s3:PutObject"}
    assert len(_as_list(stmt["Action"])) == 2
    covered = _s3_prefix_names(stmt)
    assert covered == REQUIRED_PREFIXES | {"users"}, (
        f"S3 prefixes must be exactly legacy 8 + users, got {sorted(covered)}"
    )
    assert len(_as_list(stmt["Resource"])) == 9


def test_iam_list_bucket_prefix_condition_is_exact():
    """Req 18.4: ListBucket only for raw-posts/*, users/*, generated-posts/*."""
    policy = _load_policy()
    stmt = _stmt_by_sid(policy["Statement"], "ListRawPosts")
    assert _as_list(stmt["Action"]) == ["s3:ListBucket"]
    assert _bucket_level_resource(stmt)
    assert set(_list_prefix_values(stmt)) == LIST_PREFIX_VALUES
    assert len(_list_prefix_values(stmt)) == len(LIST_PREFIX_VALUES)


def test_iam_delete_limited_to_three_patterns():
    """Req 18.5 / 18.11: s3:DeleteObject only on the three allowed patterns."""
    policy = _load_policy()
    _assert_delete_statement(policy["Statement"], _norm_policy_resource)


def test_iam_delete_never_matches_legacy_or_other_user_keys():
    """Req 18.5 / 18.11: legacy prefixes and non-draft user data are undeletable."""
    policy = _load_policy()
    patterns = [
        _norm_policy_resource(r)
        for r in _as_list(_stmt_by_sid(policy["Statement"], "DeleteUserObjects")["Resource"])
    ]
    _assert_delete_matching(patterns)


def test_iam_ssm_statement_is_exactly_two_distinct_parameters():
    """Req 7.1 / 18.8: ssm:GetParameter on exactly token + signing-secret paths."""
    policy = _load_policy()
    stmt = _stmt_by_sid(policy["Statement"], "ReadToken")
    assert _as_list(stmt["Action"]) == ["ssm:GetParameter"]
    resources = [_norm_policy_resource(r) for r in _as_list(stmt["Resource"])]
    assert len(resources) == 2 and len(set(resources)) == 2
    assert set(resources) == {
        "arn:aws:ssm:us-east-1:${AccountId}:parameter${MablopTokenSsmPath}",
        "arn:aws:ssm:us-east-1:${AccountId}:parameter${MablopSigningSecretSsmPath}",
    }


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
    """Req 9.x: regional ARNs in the role policy reference us-east-1 only.

    The only exception is the Bedrock foundation-model ARNs for the other
    regions that the US cross-region inference profile routes to
    (us-east-2, us-west-2); every other ARN must be us-east-1.
    """
    policy = _load_policy()
    profile_destinations = {"us-east-2", "us-west-2"}
    others = set()
    for stmt in policy["Statement"]:
        for res in _as_list(stmt.get("Resource")):
            for r in re.findall(r"\b(?:us|eu|ap|sa|ca|me|af)-[a-z]+-\d\b", res):
                if r == "us-east-1":
                    continue
                is_profile_destination = (
                    stmt.get("Sid") == "BedrockInvoke"
                    and r in profile_destinations
                    and res.startswith(f"arn:aws:bedrock:{r}::foundation-model/")
                )
                if not is_profile_destination:
                    others.add(r)
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


def _template_statements(template=None):
    role = _lambda_role(template or _load_template())
    return role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]


def _function(template):
    funcs = [
        r for r in template["Resources"].values() if r.get("Type") == "AWS::Serverless::Function"
    ]
    assert len(funcs) == 1, "expected exactly one AWS::Serverless::Function"
    return funcs[0]


EXPECTED_SIDS = {
    "BedrockInvoke",
    "ListRawPosts",
    "S3Prefixes",
    "DeleteUserObjects",
    "LogsOnly",
    "ReadToken",
}


def test_template_statement_sids_exact_and_no_wildcards():
    """Req 18.4 / 18.5: template role has exactly the expected statements, no '*'."""
    stmts = _template_statements()
    sids = [s.get("Sid") for s in stmts]
    assert len(sids) == len(set(sids)) and set(sids) == EXPECTED_SIDS
    assert {s["Sid"] for s in _load_policy()["Statement"]} == EXPECTED_SIDS
    for s in stmts:
        assert s.get("Effect") == "Allow"
        for a in _as_list(s.get("Action")):
            assert a != "*" and not a.endswith(":*"), f"{s['Sid']}: wildcard action {a}"
        for r in _as_list(s.get("Resource")):
            assert _norm_template_resource(r) != "*", f"{s['Sid']}: wildcard resource"


def test_template_s3_prefixes_are_legacy_eight_plus_users():
    """Req 18.4: S3Prefixes is Get/Put on exactly 9 prefixes (legacy 8 + users)."""
    stmt = _stmt_by_sid(_template_statements(), "S3Prefixes")
    assert sorted(_as_list(stmt["Action"])) == ["s3:GetObject", "s3:PutObject"]
    assert _s3_prefix_names(stmt) == REQUIRED_PREFIXES | {"users"}
    assert len(_as_list(stmt["Resource"])) == 9


def test_template_list_bucket_prefix_condition_is_exact():
    """Req 18.4: ListRawPosts keeps its Sid and lists raw-posts/*, users/*, generated-posts/*."""
    stmt = _stmt_by_sid(_template_statements(), "ListRawPosts")
    assert _as_list(stmt["Action"]) == ["s3:ListBucket"]
    assert _bucket_level_resource(stmt)
    assert set(_list_prefix_values(stmt)) == LIST_PREFIX_VALUES
    assert len(_list_prefix_values(stmt)) == len(LIST_PREFIX_VALUES)


def test_template_delete_limited_to_three_patterns():
    """Req 18.5 / 18.11: template delete grant is exactly the three patterns."""
    stmts = _template_statements()
    _assert_delete_statement(stmts, _norm_template_resource)
    patterns = [
        _norm_template_resource(r)
        for r in _as_list(_stmt_by_sid(stmts, "DeleteUserObjects")["Resource"])
    ]
    _assert_delete_matching(patterns)


def test_template_ssm_statement_is_exactly_two_distinct_parameters():
    """Req 7.1 / 18.8: template ssm:GetParameter covers token + signing-secret paths only."""
    stmt = _stmt_by_sid(_template_statements(), "ReadToken")
    assert _as_list(stmt["Action"]) == ["ssm:GetParameter"]
    resources = [_norm_template_resource(r) for r in _as_list(stmt["Resource"])]
    assert len(resources) == 2 and len(set(resources)) == 2
    assert set(resources) == {
        "arn:aws:ssm:us-east-1:${AccountId}:parameter${MablopTokenSsmPath}",
        "arn:aws:ssm:us-east-1:${AccountId}:parameter${MablopSigningSecretSsmPath}",
    }


def test_template_role_mirrors_policy_json_for_s3_and_ssm():
    """Req 18.10: template statements and the JSON mirror grant the same S3/SSM access."""
    template_stmts = _template_statements()
    policy_stmts = _load_policy()["Statement"]
    for sid in ("ListRawPosts", "S3Prefixes", "DeleteUserObjects", "ReadToken"):
        t = _stmt_by_sid(template_stmts, sid)
        p = _stmt_by_sid(policy_stmts, sid)
        assert t["Effect"] == p["Effect"], sid
        assert sorted(_as_list(t["Action"])) == sorted(_as_list(p["Action"])), sid
        assert sorted(_norm_template_resource(r) for r in _as_list(t["Resource"])) == sorted(
            _norm_policy_resource(r) for r in _as_list(p["Resource"])
        ), sid
        assert t.get("Condition") == p.get("Condition"), sid


def test_function_url_cors_unchanged():
    """Req 18.1: Function URL CORS origin, method and allowed headers are unchanged."""
    url_config = _function(_load_template())["Properties"]["FunctionUrlConfig"]
    cors = url_config["Cors"]
    assert cors["AllowOrigins"] == ["https://hyejeong-jin.github.io"]
    assert cors["AllowMethods"] == ["POST"]
    assert cors["AllowHeaders"] == ["Content-Type", "X-Mablop-Token"]
    assert url_config["AuthType"] == "NONE"


def test_bucket_cors_unchanged():
    """Req 18.1: bucket CORS rule (origin, methods, headers) is unchanged."""
    (bucket,) = _s3_bucket_resources(_load_template())
    (rule,) = bucket["Properties"]["CorsConfiguration"]["CorsRules"]
    assert rule["AllowedOrigins"] == ["https://hyejeong-jin.github.io"]
    assert rule["AllowedMethods"] == ["PUT", "GET", "HEAD"]
    assert rule["AllowedHeaders"] == ["*"]
    assert rule["ExposedHeaders"] == ["ETag"]


def test_signing_secret_parameter_and_env_var_wiring():
    """Req 7.1 / 7.4: new path parameter + env var; token parameter/env var kept."""
    template = _load_template()
    params = template["Parameters"]
    signing = params["MablopSigningSecretSsmPath"]
    token = params["MablopTokenSsmPath"]
    assert signing["Type"] == "String"
    assert signing["Default"] == "/mablop/signing-secret"
    assert token["Default"] == "/mablop/token"
    # The two SSM paths must differ (separate parameters for token and signing secret).
    assert signing["Default"] != token["Default"]
    env = _function(template)["Properties"]["Environment"]["Variables"]
    assert env["MABLOP_SIGNING_SECRET_SSM_PATH"] == {"Ref": "MablopSigningSecretSsmPath"}
    assert env["MABLOP_TOKEN_SSM_PATH"] == {"Ref": "MablopTokenSsmPath"}


def test_template_contains_no_secret_values():
    """Req 7.4 / 18.6: only SSM paths live in the template; no secret value or dynamic ref."""
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "{{resolve:" not in text, "dynamic reference would embed a secret value"
    template = _load_template()
    for name in ("MablopSigningSecretSsmPath", "MablopTokenSsmPath"):
        default = template["Parameters"][name]["Default"]
        assert re.fullmatch(r"/[A-Za-z0-9_.\-/]+", default), f"{name} default is not an SSM path"
    # Every Lambda env var is a Ref (parameter/resource), never an inline literal.
    env = _function(template)["Properties"]["Environment"]["Variables"]
    for key, value in env.items():
        assert isinstance(value, dict) and set(value) == {"Ref"}, (
            f"env var {key} must be a Ref, not a literal value"
        )
        target = value["Ref"]
        assert target in template["Parameters"] or target in template["Resources"], (
            f"env var {key} references unknown {target}"
        )
    for name, spec in template["Parameters"].items():
        assert not spec.get("NoEcho"), f"{name}: secret-style parameter in template"


EXPECTED_RESOURCE_TYPES = {
    "AWS::S3::Bucket",
    "AWS::Logs::LogGroup",
    "AWS::IAM::Role",
    "AWS::Serverless::Function",
    "AWS::Lambda::Permission",
    "AWS::SNS::Topic",
    "AWS::SNS::Subscription",
    "AWS::CloudWatch::Alarm",
}


def test_resource_type_set_unchanged():
    """Req 18.1: user-auth-admin adds no AWS resource types (no new service)."""
    types = {r.get("Type") for r in _load_template()["Resources"].values()}
    assert types == EXPECTED_RESOURCE_TYPES, (
        f"unexpected resource types: added={sorted(types - EXPECTED_RESOURCE_TYPES)} "
        f"removed={sorted(EXPECTED_RESOURCE_TYPES - types)}"
    )


def test_log_group_present():
    """Logs scope must resolve to a real log group resource."""
    template = _load_template()
    lgs = [r for r in template["Resources"].values() if r.get("Type") == "AWS::Logs::LogGroup"]
    assert lgs, "no AWS::Logs::LogGroup resource"

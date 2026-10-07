# MABLOP 배포 런북 (go-live)

> 턴키 가이드. 어떤 비밀값도 레포에 저장하지 않습니다. 배포는 GitHub Actions가
> **OIDC 단기 자격증명**으로 수행하며, 장기 AWS 액세스 키는 사용하지 않습니다 (결정 D12).
> 리전: **us-east-1** 고정. 모든 콘솔 작업은 로그인한 본인 계정에서 수행하세요.

관련 산출물:
- `infra/template.yaml` ? SAM 스택 (Lambda + Function URL + 비공개 SSE 버킷 + 최소권한 역할 + 로그그룹 + 과금 알람)
- `.github/workflows/deploy-backend.yml` ? SAM 배포 (OIDC)
- `.github/workflows/deploy-frontend.yml` ? Vite 빌드 → GitHub Pages
- `infra/github-oidc/trust-policy.json` ? 배포 역할 신뢰 정책 (플레이스홀더 치환 필요)
- `infra/github-oidc/deploy-permissions.json` ? 배포 역할 권한 정책 (플레이스홀더 치환 필요)

---

## 0. 사전 준비 (git / GitHub)

이 폴더는 아직 git 저장소가 아닙니다. OIDC는 **특정 GitHub 저장소**에 묶이므로 먼저 레포가 있어야 합니다.

```bash
# 레포 루트에서
git init
git add .
git commit -m "MABLOP MVP: backend+frontend+infra"
# GitHub에 비공개 레포 생성 후:
git branch -M main
git remote add origin https://github.com/<OWNER>/<REPO>.git
git push -u origin main
```

> 주의: `.env`, 액세스 키, SSM 토큰 등 비밀값은 커밋하지 마세요. `.gitignore`가 `.env*`를 이미 제외합니다.

GitHub Pages 사용 설정: 레포 → Settings → Pages → **Build and deployment: GitHub Actions**.

---

## 1. Bedrock 모델 액세스 활성화 (us-east-1)

콘솔 → Amazon Bedrock → **Model access** → 아래 3개 모델 액세스 요청 (승인까지 수 분~수 시간 걸릴 수 있음):

| 용도 | 모델 ID |
|---|---|
| 임베딩 | `amazon.titan-embed-text-v2:0` |
| 본문 생성 | `anthropic.claude-3-5-haiku-20241022-v1:0` |
| 문체분석/비전 | `anthropic.claude-3-haiku-20240307-v1:0` |

> 모델 ID/가격은 `docs/aidlc/inception/decisions.md` D14에 고정. 가격은 변하므로 배포 전 Bedrock 콘솔에서 재확인 권장.
> 액세스가 "Access granted"로 바뀌기 전에는 생성/임베딩 호출이 AccessDenied로 실패합니다.

---

## 2. 사전공유 토큰을 SSM에 저장 (SecureString)

Lambda가 `{{resolve:ssm-secure:/mablop/token}}`로 읽습니다. CloudShell 또는 콘솔에서:

```bash
# 강한 랜덤 토큰 생성 예시 (로컬에서):
#   openssl rand -base64 24
aws ssm put-parameter \
  --region us-east-1 \
  --name /mablop/token \
  --type SecureString \
  --value "<붙여넣을-랜덤-토큰>"
```

> 주의(솔직): CloudFormation의 `resolve:ssm-secure`는 이 값을 Lambda **환경변수에 평문**으로 넣습니다.
> 즉 Lambda 콘솔 환경변수에서 토큰이 보입니다. 소수 신뢰 사용자용 MVP라 수용 가능하나,
> 나중에 강화하려면 Lambda가 런타임에 SSM/Secrets Manager를 직접 조회하도록 바꾸세요.
> 이 토큰은 프론트엔드에서 `X-Mablop-Token` 헤더로 전송되므로 아래 3단계의 프론트 설정과 동일한 값을 공유해야 합니다.

---

## 3. GitHub OIDC 자격증명 공급자 + 배포 역할 생성 (최초 1회)

### 3a. OIDC 공급자 (계정에 없으면 1회만)

콘솔 → IAM → Identity providers → Add provider → OpenID Connect
- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

### 3b. 배포 역할 생성

1. `infra/github-oidc/trust-policy.json`에서 `<ACCOUNT_ID>`, `<GITHUB_OWNER>/<REPO>`를 치환.
   (main 브랜치 push로만 배포하도록 `sub`를 `repo:OWNER/REPO:ref:refs/heads/main`로 제한해 둠.)
2. `infra/github-oidc/deploy-permissions.json`에서 `<ACCOUNT_ID>` 치환.
3. CloudShell에서:

```bash
aws iam create-role \
  --role-name mablop-deploy \
  --assume-role-policy-document file://infra/github-oidc/trust-policy.json

aws iam put-role-policy \
  --role-name mablop-deploy \
  --policy-name mablop-deploy-permissions \
  --policy-document file://infra/github-oidc/deploy-permissions.json

# 역할 ARN 확인 (다음 단계에서 사용):
aws iam get-role --role-name mablop-deploy --query 'Role.Arn' --output text
```

---

## 4. GitHub 저장소 변수/시크릿 설정

레포 → Settings → Secrets and variables → Actions.

**Variables** (vars):
| 이름 | 값 | 쓰임 |
|---|---|---|
| `AWS_DEPLOY_ROLE_ARN` | 3b에서 얻은 `mablop-deploy` 역할 ARN | 백엔드 배포 OIDC |
| `VITE_BASE_PATH` | `/<REPO>/` (프로젝트 페이지인 경우) 또는 `/` (사용자/조직 페이지) | 프론트 라우팅 base |

**Secrets** (secrets):
| 이름 | 값 | 쓰임 |
|---|---|---|
| `VITE_MABLOP_API_URL` | (6단계에서 얻는 Function URL) | 프론트가 호출할 백엔드 |

> `VITE_MABLOP_API_URL`은 백엔드 배포가 끝나야 알 수 있으므로, 5단계(백엔드) → 값 확보 → 설정 → 7단계(프론트) 순서입니다.
> 과금 알람 이메일(`AlarmEmail`)은 SAM 파라미터로 넘깁니다(5단계 참고). 워크플로에서 넘기려면
> deploy-backend.yml의 `sam deploy`에 `--parameter-overrides AlarmEmail=<you@example.com>`를 추가하거나,
> 최초 1회 CloudShell에서 수동 배포 시 지정하세요.

---

## 5. 백엔드 배포 (SAM, OIDC)

두 가지 방법 중 택1.

### 5a. GitHub Actions (권장, 키 불필요)
`main`에 push하거나 Actions 탭에서 **deploy-backend** 워크플로를 수동 실행(workflow_dispatch).

과금 알람 이메일을 넣으려면 `.github/workflows/deploy-backend.yml`의 `sam deploy` 줄에 아래를 추가:
```
--parameter-overrides AlarmEmail=<you@example.com>
```

### 5b. CloudShell에서 최초 1회 수동 (선택)
```bash
# 레포를 CloudShell에 clone 후:
sam build --template infra/template.yaml
sam deploy --no-confirm-changeset --no-fail-on-empty-changeset \
  --stack-name mablop --region us-east-1 --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides AlarmEmail=<you@example.com>
```

배포 성공 후 출력에서 **FunctionUrl**과 **BucketName**을 확인:
```bash
aws cloudformation describe-stacks --stack-name mablop --region us-east-1 \
  --query "Stacks[0].Outputs" --output table
```

> 런타임 주의: 템플릿은 `python3.13`을 사용합니다. 특정 시점/계정에서 Lambda가 3.13을
> 아직 지원하지 않아 배포가 실패하면 `infra/template.yaml`의 `Runtime: python3.13`을
> `python3.12`로 낮추고 재배포하세요 (코드 호환됨).

---

## 6. 과금 알람 이메일 구독 확인

5단계에서 `AlarmEmail`을 지정했다면 SNS가 **구독 확인 메일**을 보냅니다. 메일의 "Confirm subscription"을 클릭해야 알람 알림을 받습니다.

> 참고: AWS/Billing EstimatedCharges 지표를 쓰려면 **Billing preferences에서 "Receive CloudWatch billing alerts"**가 켜져 있어야 합니다(계정 1회 설정).

---

## 7. 프론트엔드 배포 (GitHub Pages)

1. 5단계에서 얻은 **FunctionUrl**을 레포 Secret `VITE_MABLOP_API_URL`에 저장.
2. `VITE_BASE_PATH`를 레포 형태에 맞게 설정 (프로젝트 페이지면 `/<REPO>/`).
3. Actions 탭에서 **deploy-frontend** 실행(또는 main push). 완료되면 Pages URL이 환경에 표시됩니다.

---

## 8. 스모크 테스트

사전공유 토큰(2단계 값)과 FunctionUrl로 백엔드가 살아있는지 확인:

```bash
# 토큰 없음 -> 401 (Access_Controller 동작 확인)
curl -s -o /dev/null -w "%{http_code}\n" -X POST "<FUNCTION_URL>" \
  -H "content-type: application/json" -d '{"action":"generate-draft"}'
# -> 401

# 잘못된 액션 (토큰 유효) -> 400 bad_request
curl -s -X POST "<FUNCTION_URL>" \
  -H "content-type: application/json" -H "X-Mablop-Token: <토큰>" \
  -d '{"action":"nope"}'
# -> {"error":{"code":"bad_request",...}}
```

그다음 브라우저에서 Pages URL 접속 →
1. **스타일 학습**: 네이버 블로그 URL 입력(크롤 실패 시 본문 붙여넣기) → 학습 실행 → completed 확인.
2. **초안 작성**: 제목/개요/메모 + 이미지(선택) → 초안 생성 → Markdown 결과 확인.

> 네이버 크롤링은 iframe/동적 렌더링 때문에 자주 막힙니다(설계 리스크 R-1). 막히면
> 붙여넣기 폴백으로 본문을 직접 넣으세요. 이것이 MVP의 1차 수집 경로입니다.

---

## 9. 롤백 / 정리

```bash
# 전체 스택 삭제 (버킷에 객체가 있으면 먼저 비워야 삭제됨)
aws s3 rm s3://<BUCKET_NAME> --recursive
aws cloudformation delete-stack --stack-name mablop --region us-east-1
```

---

## 알려진 한계 / 후속 과제
- SSM 토큰이 Lambda 환경변수에 평문 노출 (MVP 수용). 강화: 런타임 SSM/Secrets Manager 조회.
- `index_store.build_and_store_index`는 빈 벡터셋(N=0)에서 예외 ? 학습 포스트가 0건이면 방어 필요.
- Bedrock 가격은 변동 ? decisions.md D14 표를 배포 시 재확인.
- 네이버 자동 크롤링 신뢰도 낮음 ? 붙여넣기 폴백이 기본 경로.
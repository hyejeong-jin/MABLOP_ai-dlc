# MABLOP 배포 런북 (go-live)

> 턴키 가이드. 어떤 비밀값도 레포에 저장하지 않습니다. 배포는 GitHub Actions가
> **OIDC 단기 자격증명**으로 수행하며, 장기 AWS 액세스 키는 사용하지 않습니다 (결정 D12).
> 리전: **us-east-1** 고정. 모든 콘솔 작업은 로그인한 본인 계정에서 수행하세요.

관련 산출물:
- `infra/template.yaml` - SAM 스택 (Lambda + Function URL + 비공개 SSE 버킷 + 최소권한 역할 + 로그그룹 + 과금 알람)
- `.github/workflows/deploy-backend.yml` - SAM 배포 (OIDC)
- `.github/workflows/deploy-frontend.yml` - Vite 빌드 → GitHub Pages
- `infra/github-oidc/trust-policy.json` - 배포 역할 신뢰 정책 (플레이스홀더 치환 필요)
- `infra/github-oidc/deploy-permissions.json` - 배포 역할 권한 정책 (플레이스홀더 치환 필요)

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
| 본문 생성 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| 문체분석/비전 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |

> 모델 ID/가격은 `docs/aidlc/inception/decisions.md` D14에 고정. 가격은 변하므로 배포 전 Bedrock 콘솔에서 재확인 권장.
> 액세스가 "Access granted"로 바뀌기 전에는 생성/임베딩 호출이 AccessDenied로 실패합니다.

---

## 2. 부트스트랩 토큰과 서명 비밀을 SSM에 저장 (SecureString)

사용자 계정 기능(user-auth-admin) 이후 SSM 파라미터는 두 개입니다. 둘 다 **적용 전 사용자 확인이 필요한 항목**입니다. 에이전트나 자동화는 이 단계를 실행하지 않습니다.

| 경로 | 용도 |
|---|---|
| `/mablop/token` | Bootstrap_Token. 최초 관리자 생성(`bootstrap-admin`)에만 쓰는 기존 사전공유 토큰. 일반 요청의 인증에는 더 이상 쓰이지 않음 |
| `/mablop/signing-secret` | Signing_Secret. 로그인 세션 토큰(HMAC-SHA256)의 서명 키. 32자 이상 무작위 문자열 |

### 2a. 부트스트랩 토큰 (기존)

Lambda는 런타임에 SSM Parameter Store에서 이 토큰을 조회합니다. 값은 Lambda 환경변수에 저장되지 않고, 경로(`/mablop/token`)만 전달됩니다. CloudShell 또는 콘솔에서 SecureString으로 저장하세요:

```bash
# 강한 랜덤 토큰 생성 예시 (로컬에서):
#   openssl rand -base64 24
aws ssm put-parameter \
  --region us-east-1 \
  --name /mablop/token \
  --type SecureString \
  --value "<붙여넣을-랜덤-토큰>"
```

> 보안: 토큰 값은 템플릿에도, Lambda 환경변수에도 저장되지 않습니다. Lambda가 요청 처리 시 `ssm:GetParameter`(WithDecryption=True)로 SecureString을 조회하고 warm 컨테이너에 캐시합니다. CloudFormation `ssm-secure` dynamic reference는 Lambda 환경변수에서 지원되지 않으므로 이 방식을 사용합니다. 실행 역할의 `ssm:GetParameter` 권한은 `/mablop/token`과 `/mablop/signing-secret` 두 파라미터에 한정됩니다(최소 권한).

### 2b. 서명 비밀 생성 (사용자 확인 필요)

CloudFormation이 만들지 않습니다. 소유자가 직접 SecureString으로 만들며, 값은 템플릿, 환경변수, 소스, 로그 어디에도 남기지 않습니다. 경로가 부트스트랩 토큰과 같거나 값이 32자 미만이면 Lambda는 세션 토큰을 발급/검증하지 않고 `internal_error`로 실패합니다.

```bash
# 32바이트 무작위 값 (base64로 44자)
#   openssl rand -base64 32
aws ssm put-parameter \
  --region us-east-1 \
  --name /mablop/signing-secret \
  --type SecureString \
  --value "<붙여넣을-랜덤-비밀-32자-이상>"
```

> 비밀을 교체하면 이미 발급된 모든 세션 토큰이 무효가 되어 전원이 다시 로그인해야 합니다(웜 컨테이너가 재생성된 뒤). 변경은 일부러 할 때만 하세요.

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

FunctionUrl로 백엔드가 살아있는지 확인합니다. 세션 토큰은 `login` 액션 응답의 `token` 값입니다(10.3 이후).

```bash
# 토큰 없음 -> 401 (Access_Controller 동작 확인)
curl -s -o /dev/null -w "%{http_code}\n" -X POST "<FUNCTION_URL>" \
  -H "content-type: application/json" -d '{"action":"generate-draft"}'
# -> 401

# 잘못된 액션 (세션 토큰 유효) -> 400 bad_request
curl -s -X POST "<FUNCTION_URL>" \
  -H "content-type: application/json" -H "X-Mablop-Token: <세션 토큰>" \
  -d '{"action":"nope"}'
# -> {"error":{"code":"bad_request",...}}
```

> 부트스트랩 토큰(`/mablop/token` 값)을 `X-Mablop-Token`에 넣어 `bootstrap-admin` 외의 액션을 호출하면 401입니다.

그다음 브라우저에서 Pages URL 접속 → 로그인 화면에서 로그인한 뒤:
1. **스타일 학습**: (멤버는 "Bedrock 사용 권한"을 선택해야 학습 버튼이 활성화됨) 네이버 블로그 URL 입력(크롤 실패 시 본문 붙여넣기) → 학습 실행 → completed 확인.
2. **초안 작성**: 제목/개요/메모 + 이미지(선택) → 초안 생성 → Markdown 결과 확인 → "결과" 탭에서 기록에 새 초안이 보이는지 확인.

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

## 10. 사용자 계정 기능(user-auth-admin) 릴리스 순서

공유 사전공유 토큰을 사용자별 계정(가입 승인, HMAC 서명 세션 토큰, 사용자별 S3 prefix, Bedrock 사용 Grant, 초안 기록)으로 바꾸는 릴리스입니다. 설계(`.kiro/specs/user-auth-admin/design.md`)의 "Operations and Release" 순서를 따릅니다.

> **(C) 표시는 소유자의 명시적 확인을 받은 뒤에만 진행하는 단계**입니다. 에이전트와 자동화는 (C) 단계를 실행하지 않습니다. 이 문서는 절차만 기록하며 어떤 환경에도 적용하지 않습니다.

| # | 단계 | 확인 |
|---|---|---|
| 1 | 코드와 아직 적용하지 않은 infra 편집을 브랜치에 병합하고 백엔드(`pytest`)와 프론트엔드(`npm test`, `npm run build`), `pytest infra/tests`, `sam validate --lint --template-file infra/template.yaml`을 로컬에서 실행 | |
| 2 | `infra/template.yaml`과 `infra/lambda-role-policy.json`의 IAM 변경 검토 | (C) |
| 3 | SSM SecureString `/mablop/signing-secret` 생성 (2b) | (C) |
| 4 | `main`에 push (백엔드 + 프론트엔드 배포 실행) | (C) |
| 5 | 운영 `bootstrap-admin` 1회 실행 | (C) |
| 6 | `jhe8888` 가입 신청 승인과 레거시 데이터 연결 확인 | (C) |
| 7 | 멤버별 첫 Bedrock 사용 Grant 승인 | (C) |
| 8 | 한참 뒤, 소유자가 결정할 때만 사후 정리 | (C) |

### 10.1 IAM 변경 검토 (단계 2, 사용자 확인 필요)

변경은 파일 편집일 뿐이며 push 전에는 어떤 IAM에도 적용되지 않습니다. 검토할 내용:

- `S3Prefixes`: Get/Put 대상에 `users/*` 추가. 기존 레거시 prefix 8개 권한은 유지.
- `ListRawPosts`(Sid 유지): `s3:ListBucket`의 `s3:prefix` 조건에 `users/*`, `generated-posts/*` 추가. 이름은 `raw-posts/*`만 허용하던 때의 것이며 지금은 좁은 이름입니다.
- `DeleteUserObjects`(신규): `s3:DeleteObject`를 `users/_auth/login/*`, `users/*/account.json`, `users/*/generated-posts/*` 세 패턴에만 허용. 레거시 prefix에는 삭제 권한이 없습니다. 이 권한으로 Lambda는 모든 사용자의 초안과 계정 객체를 삭제할 수 있어 사용자 간 분리는 애플리케이션 코드가 지킵니다. 세 패턴 중 `account.json`과 loginId 조회 패턴을 허용하지 않기로 하면 `admin-reject-user`는 `{"status": "rejected"}` 표식으로 덮어쓰는 대체 구현이 필요합니다. `delete-draft`는 대체 수단이 없습니다.
- `ReadToken`: `ssm:GetParameter` 대상에 서명 비밀 파라미터 ARN 추가(두 개). SecureString은 AWS 관리형 `aws/ssm` 키를 쓰므로 `kms:Decrypt` 추가는 필요 없습니다.
- 바뀌지 않는 것: Function URL CORS(`Content-Type`, `X-Mablop-Token`), 버킷 CORS, 과금 알람, 리소스 유형, GitHub 배포 역할.

push로 실행되는 `deploy-backend`의 `sam deploy`가 역할 정책을 적용하므로 4단계 확인이 곧 IAM 적용 확인입니다.

### 10.2 배포 (단계 4, 사용자 확인 필요)

`main`에 push하면 `deploy-backend`와 `deploy-frontend`가 모두 실행됩니다. 두 배포는 끝나는 시각이 달라 그 사이에 옛 프론트엔드가 새 백엔드를, 또는 새 프론트엔드가 옛 백엔드를 호출할 수 있고, 이때는 401 또는 400으로 닫힌 채 실패합니다. 두 경로를 동시에 허용하는 요청은 없습니다. 배포 직후 곧바로 10.3을 진행하세요.

로컬에서 `sam build`를 실행할 때 한글이 들어간 경로(이 프로젝트 경로 등)에서 실패할 수 있으므로 ASCII 스테이징 경로에서 빌드하세요. 예: `C:\mablop-deploy`에 `backend/`와 `infra/`를 복사한 뒤 그 폴더에서 `sam build --template infra/template.yaml`을 실행합니다(PowerShell 5에서는 `&&` 대신 `;` 또는 별도 명령). GitHub Actions 빌드에는 해당 없습니다.

### 10.3 운영 `bootstrap-admin` (단계 5, 사용자 확인 필요)

배포 직후 부트스트랩 토큰(`/mablop/token` 값)을 `X-Mablop-Token`에 넣어 **한 번** 호출합니다. 관리자 계정이 이미 있으면 입력을 읽기 전에 403 "이미 관리자 계정이 있습니다."로 거절됩니다. 첫 관리자가 생기기 전에는 이 토큰이 새어 나가면 누구나 관리자를 만들 수 있으므로 배포와 호출 사이의 간격을 줄이세요.

```bash
curl -s -X POST "<FUNCTION_URL>" \
  -H "content-type: application/json" \
  -H "X-Mablop-Token: <부트스트랩 토큰>" \
  -d '{"action":"bootstrap-admin","loginId":"<관리자 아이디>","name":"<이름>","password":"<비밀번호 10~128자>"}'
```

- loginId는 소문자/숫자/`_`/`-` 4~32자, 이름은 1~30자, 비밀번호는 10~128자입니다.
- 이 호출은 `account.json`(과 loginId 조회, 관리자 포인터)만 만듭니다. 레거시 데이터는 옮기지 않습니다.
- 확인: 관리자로 로그인해 "스타일 학습" 화면의 학습 상태가 0개 글, 0개 청크인지 봅니다. 관리 탭이 보여야 합니다.
- 실패 후 재시도: 중단된 관리자 부트스트랩이면 같은 loginId로 다시 호출해 이어갈 수 있습니다.
- 이 호출은 전역 실패 게이트(10분 안에 30회 실패 시 10분 차단)의 대상입니다.

### 10.4 `jhe8888` 승인과 레거시 데이터 연결 (단계 6, 사용자 확인 필요)

1. `jhe8888`이 로그인 화면의 "가입 신청" 탭에서 가입합니다(특별 처리 없음, 승인 대기 상태로 생성).
2. 관리자가 "관리" 탭의 "가입 신청"에서 승인합니다. 승인 직후 `legacy_migrator`가 **한 번** 실행되어 기존 공유 데이터를 `users/{jhe8888의 userId}/` 아래로 **복사**합니다. 원본은 지우거나 덮어쓰지 않으며, 대상에 이미 있는 객체는 덮어쓰지 않습니다(같은 크기는 건너뛰고 다른 크기는 `divergedKeys`로 보고).
   - 복사 대상: `embeddings/index.npy`, `embeddings/index-map.json`, `vector-index/manifest.json`, `style-profile/profile.json`, `metadata/learning-status.json`, `raw-posts/*.txt`, `generated-posts/*.md`
   - 복사하지 않음: 체크포인트, `images/*`, `metadata/ratelimit.json`
3. 연결에 실패하면 "관리" 화면의 해당 사용자 행 아래에 알림이 표시되고(`failed` 또는 `already_bound`일 때만), 승인은 유지됩니다. 성공이면 알림이 없습니다.
4. 확인 항목: `jhe8888`으로 로그인해 학습한 글 **9개**, 청크 **31개**가 보이는지, "결과" 탭에 이전 초안 목록이 보이는지 확인합니다. 복사된 레거시 초안의 시각은 원래 작성일이 아니라 복사 시각이고, 초안끼리는 `draftId` 순으로 정렬됩니다.
5. 같은 데이터는 한 사용자에게만 연결됩니다(`users/_auth/legacy-owner.json`). 이미 다른 사용자에게 연결되어 있으면 복사 없이 거절됩니다.

#### `migrate-legacy-data` 재시도

연결이 실패했을 때 관리자 세션 토큰으로 API를 직접 호출합니다(프론트엔드에는 재시도 버튼이 없습니다). 대상을 생략하면 loginId `jhe8888` 계정이 대상입니다. 여러 번 실행해도 안전합니다(이미 복사된 객체는 건너뜀).

```bash
curl -s -X POST "<FUNCTION_URL>" \
  -H "content-type: application/json" \
  -H "X-Mablop-Token: <관리자 세션 토큰>" \
  -d '{"action":"migrate-legacy-data"}'
# 대상 지정: '{"action":"migrate-legacy-data","targetUserId":"u_<16자리 16진수>"}'
# 응답: {"migration": {"status": "...", "copied": ..., "skipped": ..., "failedKeys": [...], "divergedKeys": [...], "message": "..."}}
```

- 첫 시도가 실패해도 바인딩 레코드는 첫 대상 사용자를 가리킨 채로 남습니다. 따라서 재시도는 **같은 사용자**에 대해서만 가능하고, 다른 사용자 id로 호출하면 403 "레거시 데이터는 이미 다른 사용자에게 연결되어 있습니다."로 거절됩니다.
- `failedKeys`가 비어 있고 `status`가 `succeeded`가 될 때까지 반복한 뒤 10.4의 확인 항목을 다시 봅니다.

### 10.5 Bedrock 사용 Grant (단계 7, 사용자 확인 필요)

멤버는 Grant 없이는 Bedrock 액션(`learn-style`, `analyze-images`, `generate-draft`, `rebuild-from-stored`, 그리고 `get-result`의 비-초안 종류)을 호출할 수 없습니다(403). 멤버가 "내 정보"에서 사용 신청을 하면 관리자가 "관리" > "권한 관리"에서 만료 기간(7/30/90일)을 골라 승인합니다. 관리자 자신은 Grant가 필요 없습니다. 폐기는 다음 호출부터 적용됩니다(이미 시작된 긴 `learn-style` 실행은 끝까지 진행). Grant에는 사용량 한도가 없습니다.

### 10.6 사후 정리 (단계 8, 사용자 확인 필요)

한참 뒤 소유자가 결정할 때만 합니다. 이 릴리스에서는 하지 않습니다.

- 마이그레이션 검증 후 원본 레거시 객체 삭제
- 정책에서 레거시 prefix 권한 제거
- `/mablop/token` 삭제 여부 결정(삭제하면 `bootstrap-admin`을 더 호출할 수 없음)

### 10.7 운영 중 알아둘 점

- 로그인 실패: 같은 아이디로 10분 안에 5회 실패하면 10분 차단되고, 전체 실패가 10분 안에 30회에 이르면 모든 로그인이 10분 차단됩니다. 관리자의 `admin-reset-password`가 해당 아이디의 카운터를 초기화합니다.
- 세션 토큰 수명은 8시간이며 갱신 액션이 없습니다. 만료되면 다시 로그인합니다.
- 계정을 비활성화하거나 비밀번호를 바꾸면 그 전에 발급된 토큰은 즉시 무효가 됩니다. 로그아웃은 토큰을 폐기하지 않습니다.
- 임시 비밀번호(`admin-reset-password`)는 응답에 한 번만 나오고 저장되지 않습니다.

---

## 알려진 한계 / 후속 과제
- 부트스트랩 토큰과 서명 비밀은 런타임 SSM 조회로 처리되어 Lambda 환경변수에 노출되지 않습니다(환경변수에는 경로만).
- `index_store.build_and_store_index`는 빈 벡터셋(N=0)에서 예외 - 학습 포스트가 0건이면 방어 필요.
- Bedrock 가격은 변동 - decisions.md D14 표를 배포 시 재확인.
- 네이버 자동 크롤링 신뢰도 낮음 - 붙여넣기 폴백이 기본 경로.


### Naver automatic collection policy

MABLOP first tries the public Naver RSS feed (`https://rss.blog.naver.com/<blogId>.xml`) to discover public post links, then fetches public post pages. If page extraction fails, the public RSS description may be used as a limited fallback. The system does not bypass login, robots rules, private posts, or access controls; paste the full body when the public content is unavailable.

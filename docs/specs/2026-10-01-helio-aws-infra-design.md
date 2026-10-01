# Helio — Infraestrutura AWS em CloudFormation

## Context
O Helio tem três subprojetos sem nenhuma infra de nuvem: `edge/` (detector de sonolência em Python, que já publica uma mensagem de teste no IoT Core via `boto3 iot-data`), `dashboard/` (React + Vite, 100% mockado com MSW e com login falso) e `onboard/` (demo isolada, fora do escopo). O pedido é criar a infra em CloudFormation com Cognito, DynamoDB (viagens e motoristas) e Lambda + SNS para e-mails de aviso, e integrar o edge e o dashboard a ela.

Decisões do brainstorm:
- **Objetivo:** projeto acadêmico/demo. Um ambiente só, custo mínimo (on-demand, sem VPC/NAT), `us-east-1`.
- **Ingestão:** IoT Core com Thing + certificado X.509 e Topic Rule para a λ ingest.
- **API:** núcleo apenas (drivers, trips, events, settings). Vehicles, devices e MLOps continuam no mock.
- **E-mails:** só para os gestores, com os endereços passados como parâmetro do stack.
- **Extras:** hospedar o dashboard (S3 + CloudFront), IoT Thing + certificado, resumo diário.
- **Estrutura:** nested stacks, deploy com `aws cloudformation package` + `deploy`.
- **Escopo:** a integração no edge e no dashboard entra no plano.
- **Lambdas:** Python 3.13.

## Arquitetura
```
edge ──MQTT (cert)──► IoT Core ──Rule──► λ ingest ──► DynamoDB (Trips, Events)
                                            └─ severidade ≥ notifyOn ─► SNS ─► e-mail gestores
EventBridge Scheduler (cron 20:00 America/Sao_Paulo) ──► λ daily_summary ──► SNS
browser ──► CloudFront ─┬─ /*     ──► S3 (dashboard build, OAC)
                        └─ /api/* ──► HTTP API (JWT Cognito) ──► λ api_* ──► DynamoDB
```

## Layout (novo `infra/`, com o próprio `git init`, como os irmãos)
```
infra/
  root.yaml                 # Parameters: ProjectName=helio, AlertEmails (CommaDelimitedList), DeviceCsr, DeviceThingName=HELIO_V1.0
  stacks/auth.yaml  data.yaml  alerts.yaml  iot.yaml  api.yaml  web.yaml
  src/                      # código de todas as Lambdas; cada função muda só o Handler
    common/{db.py, http.py, auth.py, severity.py, serialize.py}
    ingest.py  daily_summary.py  api_drivers.py  api_trips.py  api_events.py  api_settings.py
  tests/                    # pytest + moto
  scripts/deploy.sh  scripts/make-device-cert.sh  scripts/seed.py  scripts/smoke.sh  scripts/deploy-dashboard.sh
  README.md
```
`root.yaml` referencia os filhos com `AWS::CloudFormation::Stack` e `TemplateURL: stacks/x.yaml`. O `package` reescreve esses caminhos para URLs do S3 e sobe o `src/`. Os outputs dos filhos chegam como parâmetros aos stacks dependentes. A ordem de dependência é auth → data → alerts → iot → api → web, porque o web precisa do domínio da API.

## Stacks
- **auth.yaml:**
  - `AWS::Cognito::UserPool`: login por e-mail, `AllowAdminCreateUserOnly: true`, política de senha padrão.
  - `UserPoolClient`: sem secret, `ALLOW_USER_SRP_AUTH` + `ALLOW_REFRESH_TOKEN_AUTH`.
  - `UserPoolGroup` × 3: `Administrador`, `GestorDeFrota`, `Operador`.
  - Outputs: `UserPoolId`, `UserPoolClientId`, `Issuer`.
- **data.yaml:** quatro tabelas `PAY_PER_REQUEST`, com `DeletionPolicy: Retain` desligado (é demo, então `Delete`).
  - `Drivers`: PK `driverId`; GSI `byDevice` (`assignedDeviceId`, esparso).
  - `Trips`: PK `tripId` (= `ride_id`); GSI `byDriver` (`driverId`, `startedAt`).
  - `Events`: PK `eventId`; GSIs `byTrip` (`tripId`, `timestamp`), `byDriver` (`driverId`, `timestamp`), `byDay` (`day` = `YYYY-MM-DD` BRT, `timestamp`).
  - `Settings`: PK `settingId`, com o item `thresholds` = `{mild, drowsy, critical, notifyOn, emailAlerts}`. O valor inicial vem do `seed.py`, e o código usa defaults se o item não existir.
- **alerts.yaml:**
  - `AWS::SNS::Topic` com uma `Subscription` de e-mail por endereço. Como CloudFormation não faz loop, a lista é limitada a até 3 e-mails via `Fn::Select` + `Conditions`; isso fica documentado.
  - `daily_summary`: Function + Role + `AWS::Scheduler::Schedule` + LogGroup (14 dias).
- **iot.yaml:**
  - `AWS::IoT::Thing`.
  - `AWS::IoT::Policy`: `iot:Connect` em `client/${iot:Connection.Thing.ThingName}` e `iot:Publish` em `topic/helio/devices/${iot:Connection.Thing.ThingName}/events`.
  - `AWS::IoT::Certificate` a partir de `DeviceCsr`, com `Status: ACTIVE`, mais `PolicyPrincipalAttachment` e `ThingPrincipalAttachment`.
  - `TopicRule`: `SELECT *, topic(3) AS thingName FROM 'helio/devices/+/events'` → Lambda, com `errorAction` para o CloudWatch Logs.
  - λ `ingest` + `AWS::Lambda::Permission` para `iot.amazonaws.com`.
  - Output: `IotEndpoint`. O CloudFormation não expõe esse valor, então o `deploy.sh` obtém com `aws iot describe-endpoint --endpoint-type iot:Data-ATS`.
- **api.yaml:**
  - `AWS::ApiGatewayV2::Api` (HTTP) e um `Authorizer` JWT (issuer = pool, audience = client id).
  - Rotas e Integrations `AWS_PROXY` para 4 Lambdas, cada uma com Role de menor privilégio (só as tabelas e ações que usa) e LogGroup de 14 dias.
  - Stage `$default` com auto-deploy.
- **web.yaml:**
  - Bucket S3 privado (`BlockPublicAccess`).
  - CloudFront com OAC. Default behavior no S3 com uma `CloudFront::Function` que reescreve rotas sem extensão para `/index.html` (fallback da SPA). Behavior `/api/*` na origem da HTTP API, com `CachingDisabled` e `AllViewerExceptHostHeader`.
  - Outputs: `BucketName`, `DistributionId`, `DashboardUrl`.

## Contrato de ingestão (edge → IoT)
Tópico: `helio/devices/{thingName}/events`. Payload, estendendo o `Event` atual:
```json
{"device_id":"HELIO_V1.0","ride_id":"<hmac>","timestamp":1767225600,"nonce":"a1b2c3d4",
 "data":[{"timestamp":"2026-10-01T22:15:03-03:00","score":0.72,"ear":0.13,"perclos":0.41,"status":"ALERTA","durationSec":2.4}]}
```
Para cada item em `data`, a λ ingest:
1. Valida o item; se for inválido, registra no log e descarta, sem lançar exceção.
2. Converte o score para `round(score*100)`.
3. Calcula a severidade pelos limiares de Settings (`<mild` → `alert`, `<drowsy` → `mild`, `<critical` → `drowsy`, senão `critical`).
4. Busca o motorista no GSI `Drivers.byDevice`, com `driverId=null` se não houver.
5. Grava o evento com `eventId = sha256(ride_id:nonce:item.timestamp)[:16]`, com `ConditionExpression attribute_not_exists` para ser idempotente.
6. Faz upsert da Trip: `if_not_exists(startedAt)`, `lastEventAt`, `alertCount + 1`, `maxScore` (atualizado com a condição de que o novo score seja maior, em duas operações).
7. Se `emailAlerts` estiver ligado e a severidade for ≥ `notifyOn`, publica no SNS.

Triggers derivados: `perclos` se perclos ≥ 0.3, `eye-closure` se ear < `EAR_THRESHOLD`. `location` = null, porque o edge não tem GPS e o dashboard precisa tolerar isso.

## API (respostas em camelCase, no formato de `dashboard/src/api/types.ts`; erros como `{message}`)
| Rota | Lambda | Grupos |
|---|---|---|
| GET `/api/drivers`, `/api/drivers/{id}`, `/api/drivers/{id}/history` | api_drivers | todos |
| POST `/api/drivers`, PUT `/api/drivers/{id}` | api_drivers | Administrador, GestorDeFrota |
| GET `/api/trips?driverId&from&to`, GET `/api/trips/{id}` (inclui os eventos) | api_trips | todos |
| GET `/api/events` (page, pageSize, severity, driverId, acknowledged, from, to, sort, dir), GET `/api/events/{id}`, POST `/api/events/{id}/acknowledge` | api_events | todos; `acknowledgedBy` vem do claim `email` |
| GET `/api/settings/thresholds` / PUT | api_settings | GET para todos / PUT só Administrador |

`common/auth.py` lê `requestContext.authorizer.jwt.claims["cognito:groups"]` e responde 403 com `{message}`. `/api/events` consulta o `byDay` para cada dia do intervalo (padrão: últimos 7 dias), filtra, ordena e pagina em memória, o que basta para o volume de uma demo. `/drivers/{id}/history` agrega os scores dos eventos do motorista por hora.

## Integração — edge (`edge/`, repositório próprio, branch `feat/aws-iot`)
- `pyproject.toml`: adicionar `awsiotsdk`.
- `src/helpers/aws.py`: trocar o `boto3 iot-data` por um cliente MQTT5 (`awsiot.mqtt5_client_builder.mtls_from_path`) configurado por variáveis de ambiente: `HELIO_IOT_ENDPOINT`, `HELIO_CERT_PATH`, `HELIO_KEY_PATH`, `HELIO_CA_PATH`. Manter o método `send_payload(Event)` e adicionar `connect`/`close`. Se as variáveis não existirem, o edge segue só com o log local, sem quebrar.
- `src/helpers/consts.py`: `DEVICE_ID` passa a vir de `HELIO_THING_NAME`, com default `HELIO_V1.0`.
- `src/app.py`:
  - Remover o publish de teste e usar o tópico `helio/devices/{DEVICE_ID}/events`.
  - Gerar o `ride_id` uma vez por execução, que é a viagem.
  - Em `log_alert`, publicar também um `Event` com o item do contrato (timestamp ISO com fuso e `durationSec`).
  - Conferir e corrigir os imports `helpers.*` / `models.*` versus `src.*`, rodando `python main.py` antes de mudar qualquer coisa.
- `src/event_logger.py`: gravar o timestamp com fuso (`datetime.now().astimezone()`).
- Certificados: `infra/scripts/make-device-cert.sh` gera a chave e o CSR com `openssl` (a chave nunca sai da máquina), e o CSR vai como parâmetro do stack. Depois do deploy, o script baixa o cert (`aws iot describe-certificate`) e a Amazon Root CA 1 para `edge/certs/`, que entra no `.gitignore`.

## Integração — dashboard (`dashboard/`, repositório próprio, branch `feat/cognito-api`)
- Dependência: `amazon-cognito-identity-js`.
- `src/config/env.ts` (novo): lê `VITE_API_MODE` (`mock` | `hybrid`), `VITE_COGNITO_USER_POOL_ID` e `VITE_COGNITO_CLIENT_ID`.
- `src/features/auth/cognito.ts` (novo):
  - `signIn(email, password)` via SRP, tratando o desafio `NEW_PASSWORD_REQUIRED` (usuários criados pelo admin).
  - `getIdToken()`, que renova a sessão quando preciso.
  - `signOut()`.
- `auth-context.tsx`:
  - No modo `mock`, mantém o comportamento atual.
  - No modo `hybrid`, usa `cognito.ts` e mapeia `cognito:groups` para `role` (`GestorDeFrota` → "Gestor de Frota" etc.).
  - `signIn` passa a ser async e recebe a senha. Ajustar a página `/entrar` e os testes.
- `src/api/client.ts`: incluir `Authorization: Bearer <idToken>` quando houver token; em 401, faz `signOut` e redireciona.
- `src/api/mock/handlers.ts` + `browser.ts`: no modo `hybrid`, excluir os handlers de drivers, events e settings (passam para a API real via `onUnhandledRequest: "bypass"`) e manter os demais.
- `src/main.tsx`: só iniciar a `startLiveSimulation` no modo `mock`.
- `src/api/types.ts`: aceitar `location: {lat,lng} | null` no `DrowsinessEvent` e `driverId: string | null`. Adicionar o tipo `Trip` e as queries `useTrips` / `useTrip` em `queries.ts`. Uma página de viagens fica fora do escopo; a lista de viagens aparece na página do motorista.
- `infra/scripts/deploy-dashboard.sh`:
  1. Lê os outputs do stack.
  2. Escreve `dashboard/.env.production.local`.
  3. Roda `npm run build`.
  4. Faz `aws s3 sync dist/` com `--delete`.
  5. Roda `cloudfront create-invalidation /*`.

## Scripts
- `deploy.sh`: `cfn-lint` → cria o bucket de artefatos, se não existir → `aws cloudformation package` → `deploy --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND` → imprime os outputs e o endpoint IoT.
- `seed.py`: cria o item `thresholds` padrão (mild 30, drowsy 60, critical 80, notifyOn `drowsy`, emailAlerts true), dois motoristas de exemplo (um vinculado ao `HELIO_V1.0`) e um usuário admin no Cognito (`admin-create-user` + `admin-add-user-to-group`).
- `smoke.sh`: `aws iot-data publish` de um payload `ALERTA` no tópico, depois confere o evento e a trip no DynamoDB e o envio de e-mail no log da Lambda.

## Ordem de execução
Usar o skill writing-plans para detalhar as tarefas, com TDD (superpowers:test-driven-development) nos handlers.
1. `infra/` com `git init`, salvar este design em `infra/docs/specs/2026-10-01-helio-aws-infra-design.md` e fazer commit.
2. `src/common` + `ingest` + testes → `data.yaml`, `alerts.yaml`, `iot.yaml`, `root.yaml` → deploy → smoke.
3. `daily_summary` + agendamento.
4. `auth.yaml` + handlers da API + testes → `api.yaml` → deploy → teste com `curl` e JWT do admin.
5. `web.yaml` → deploy.
6. Integração no edge (branch) → teste real com webcam.
7. Integração no dashboard (branch) → `deploy-dashboard.sh` → login e telas.
8. `infra/README.md`: pré-requisitos, deploy, confirmação dos e-mails do SNS, custos esperados (~US$0 no free tier) e teardown (`aws cloudformation delete-stack`, depois esvaziar o bucket).

## Verificação
- `cfn-lint infra/root.yaml infra/stacks/*.yaml` sem erros.
- `pytest infra/tests`: cobre normalização e severidade, idempotência do ingest, upsert da trip, decisão de enviar e-mail, 403 por grupo, filtros e paginação de `/api/events`, acknowledge com o e-mail do token.
- `deploy.sh` termina em `CREATE_COMPLETE`, e o e-mail de confirmação do SNS chega.
- `smoke.sh`: o evento aparece em Events, a Trip tem `alertCount` 1 e o e-mail "[Helio] Sonolência…" chega.
- `curl` sem token em `/api/drivers` → 401. Com o JWT de um Operador, `PUT /api/settings/thresholds` → 403; com o do Administrador → 200.
- Edge com o cert real: um alerta na webcam gera evento no DynamoDB e e-mail.
- Dashboard no CloudFront: login com o admin do seed (troca de senha), lista de alertas com o evento real, reconhecer alerta, `npm test` e `npm run lint` passando, e as telas mockadas (veículos, MLOps) continuando funcionais.
- Para o resumo diário, `aws lambda invoke` manual → e-mail do resumo.

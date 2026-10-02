# Helio — Infraestrutura AWS

CloudFormation (nested stacks) da nuvem do Helio. Design: `docs/specs/2026-10-01-helio-aws-infra-design.md`.

| Stack | Conteúdo |
|---|---|
| `stacks/auth.yaml` | Cognito: User Pool, App Client, grupos Administrador / GestorDeFrota / Operador |
| `stacks/data.yaml` | DynamoDB: drivers, trips, events, settings |
| `stacks/api.yaml` | HTTP API + autorizador JWT + Lambdas `api_*` |
| `stacks/web.yaml` | S3 privado + CloudFront (dashboard e `/api/*`) |
| `stacks/alerts.yaml` | SNS (e-mails dos gestores) + resumo diário às 20h |
| `stacks/iot.yaml` | Thing, certificado, policy, regra IoT → Lambda `ingest` |

## Código anterior
`lambda/event_processor/` é a Lambda anterior (snapshots no S3 + tabela `HelioDriveEvents`, criada por `setup_aws.py`). Ela não faz parte destes stacks e não é usada por eles.

## Pré-requisitos
AWS CLI v2 autenticada (`aws login`), [uv](https://docs.astral.sh/uv/), openssl, jq.

## Testes
```bash
uv sync
uv run pytest
uv run cfn-lint root.yaml stacks/*.yaml
```

## Deploy
```bash
ALERT_EMAIL_1=gestor@exemplo.com scripts/deploy.sh   # até 3: ALERT_EMAIL_2, ALERT_EMAIL_3
uv run python scripts/seed.py --admin-email voce@exemplo.com
scripts/smoke.sh
```
- Cada gestor precisa clicar no link "Confirm subscription" que a AWS envia por e-mail.
- O admin recebe uma senha temporária e escolhe a definitiva no primeiro login.
- As variáveis do edge ficam em `certs/edge.env`. A pasta `certs/` contém a chave privada e não vai para o git.

## Contrato do dispositivo
Tópico `helio/devices/{thingName}/events` (o `client_id` MQTT deve ser igual ao `thingName`):
```json
{"device_id": "helio-edge-01", "ride_id": "<hmac>", "timestamp": 1790000000, "nonce": "a1b2c3d4",
 "data": [{"timestamp": "2026-10-01T22:15:03-03:00", "score": 0.72, "ear": 0.13, "perclos": 0.41, "status": "ALERTA", "durationSec": 2.4}]}
```

## Custos
Tudo é sob demanda (DynamoDB on-demand, Lambda, HTTP API, SNS, IoT Core, CloudFront PriceClass_100). No volume de uma demo, fica no free tier, perto de US$ 0/mês.

## Remover tudo
```bash
aws s3 rm "s3://$(aws cloudformation describe-stacks --stack-name helio --query "Stacks[0].Outputs[?OutputKey=='SiteBucketName'].OutputValue" --output text)" --recursive
aws cloudformation delete-stack --stack-name helio
aws cloudformation wait stack-delete-complete --stack-name helio
```
O bucket de artefatos `helio-artifacts-<conta>-<região>` não faz parte do stack; apague-o à mão se quiser.

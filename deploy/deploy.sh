#!/usr/bin/env bash
# Builds this repo and deploys it as a Cloud Run service.
# Run from Cloud Shell, from the repo root: bash deploy/deploy.sh
# The first run asks for the Google sign-in client and who may sign in.
# Later runs ask for nothing and only roll out new code.
set -euo pipefail

PROJECT=wigo-ads-mcp
NUM=17668607738
REGION=europe-north1
SERVICE=messaging-mcp
URL="https://${SERVICE}-${NUM}.${REGION}.run.app"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
TAG="$(date +%Y%m%d-%H%M%S)"
IMG="${REGION}-docker.pkg.dev/${PROJECT}/mcp-servers/${SERVICE}:${TAG}"
RUN_SA_NAME=messaging-mcp-run
RUN_SA="${RUN_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
SESSION_DB=messaging-sessions
CLIENT_SECRET_ID=messaging-oauth-client-secret
SIGNING_KEY_ID=messaging-jwt-signing-key
ACCOUNTS_ID=messaging-accounts

gcloud config set project "$PROJECT" --quiet
test -f "$SRC/messaging_mcp/server.py" || { echo "messaging_mcp saknas i $SRC"; exit 1; }

put_secret() { # name, value on stdin
  if gcloud secrets describe "$1" >/dev/null 2>&1; then
    gcloud secrets versions add "$1" --data-file=- >/dev/null
  else
    gcloud secrets create "$1" --replication-policy=automatic --data-file=- >/dev/null
  fi
}

echo "== 1/6 Tjanster och korkonto"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com firestore.googleapis.com artifactregistry.googleapis.com --quiet
gcloud iam service-accounts describe "$RUN_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$RUN_SA_NAME" --display-name="Messaging MCP runtime" --quiet
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${RUN_SA}" \
  --role=roles/datastore.user --condition=None --quiet >/dev/null

echo "== 2/6 Inloggning med Google"
# Values that contain commas need another separator than gcloud's default.
ENV_VARS="MESSAGING_MCP_BASE_URL=${URL}##MESSAGING_MCP_FIRESTORE_PROJECT=${PROJECT}##MESSAGING_MCP_FIRESTORE_DATABASE=${SESSION_DB}"
if gcloud run services describe "$SERVICE" --region="$REGION" >/dev/null 2>&1 \
   && gcloud secrets describe "$CLIENT_SECRET_ID" >/dev/null 2>&1; then
  echo "Tjansten finns redan. Inloggningsklient och tillatna adresser behalls."
else
  echo "Skapa en OAuth-klient (Web application) under Google Auth Platform > Clients"
  echo "med denna Authorized redirect URI: ${URL}/auth/callback"
  read -r -p "Client ID: " CID
  read -r -s -p "Client secret (visas inte): " CSEC; echo
  ME="$(gcloud config get-value account 2>/dev/null)"
  read -r -p "E-postadresser som far logga in, med komma emellan [${ME}]: " ALLOWED
  ALLOWED="${ALLOWED:-$ME}"
  test -n "$CID" && test -n "$CSEC" && test -n "$ALLOWED" || { echo "Alla tre uppgifterna kravs"; exit 1; }
  printf %s "$CSEC" | put_secret "$CLIENT_SECRET_ID"
  unset CSEC
  ENV_VARS="${ENV_VARS}##MESSAGING_MCP_OAUTH_CLIENT_ID=${CID}##MESSAGING_MCP_ALLOWED_EMAILS=${ALLOWED// /}"
fi
gcloud secrets describe "$SIGNING_KEY_ID" >/dev/null 2>&1 || \
  openssl rand -hex 32 | tr -d '\n' | put_secret "$SIGNING_KEY_ID"
# The account list starts empty. Accounts are added with deploy/add-account.sh.
gcloud secrets describe "$ACCOUNTS_ID" >/dev/null 2>&1 || printf '{}' | put_secret "$ACCOUNTS_ID"
for SECRET in "$CLIENT_SECRET_ID" "$SIGNING_KEY_ID" "$ACCOUNTS_ID"; do
  gcloud secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:${RUN_SA}" \
    --role=roles/secretmanager.secretAccessor --quiet >/dev/null
done

echo "== 3/6 Egen databas for inloggningar"
gcloud firestore databases describe --database="$SESSION_DB" >/dev/null 2>&1 || \
  gcloud firestore databases create --database="$SESSION_DB" --location=eur3 --quiet

echo "== 4/6 Bygger $TAG (tar nagra minuter)"
gcloud artifacts repositories describe mcp-servers --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create mcp-servers --repository-format=docker --location="$REGION" --quiet
gcloud builds submit "$SRC" --tag "$IMG" --quiet

echo "== 5/6 Rullar ut"
gcloud run deploy "$SERVICE" --image="$IMG" --region="$REGION" --platform=managed \
  --service-account="$RUN_SA" --allow-unauthenticated --max-instances=1 --memory=1Gi \
  --timeout=600 \
  --update-env-vars="^##^${ENV_VARS}" \
  --update-secrets="MESSAGING_MCP_OAUTH_CLIENT_SECRET=${CLIENT_SECRET_ID}:latest,MESSAGING_MCP_JWT_SIGNING_KEY=${SIGNING_KEY_ID}:latest,MESSAGING_MCP_ACCOUNTS=${ACCOUNTS_ID}:latest" \
  --quiet

echo "== 6/6 Kontroll"
echo "Tjanstens adress: $(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
echo "Forvantad adress: $URL"
echo "HTTP-status utan inloggning (401 ar ratt): $(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/mcp")"
echo "Redirect URI som ska finnas i OAuth-klienten: $URL/auth/callback"
echo "KLART $TAG  MCP-adress: $URL/mcp"

#!/usr/bin/env bash
# Adds an Infobip account to the server, or replaces the key of an existing one.
# Run from Cloud Shell: bash deploy/add-account.sh
# The API key is typed at a hidden prompt and stored only in Secret Manager.
set -euo pipefail

PROJECT=wigo-ads-mcp
REGION=europe-north1
SERVICE=messaging-mcp
ACCOUNTS_ID=messaging-accounts

gcloud config set project "$PROJECT" --quiet
CURRENT="$(gcloud secrets versions access latest --secret="$ACCOUNTS_ID")"
echo "Konton som finns nu:"
printf %s "$CURRENT" | jq -r 'to_entries[] | "  \(.key)  (\(.value.base_url))"'

read -r -p "Kortnamn pa kontot (sma bokstaver, siffror, bindestreck): " ALIAS
[[ "$ALIAS" =~ ^[a-z0-9][a-z0-9-]{0,39}$ ]] || { echo "Ogiltigt kortnamn"; exit 1; }
if printf %s "$CURRENT" | jq -e --arg a "$ALIAS" 'has($a)' >/dev/null; then
  echo "Kontot '$ALIAS' finns redan: $(printf %s "$CURRENT" | jq -r --arg a "$ALIAS" '.[$a].base_url')"
  read -r -p "Byta dess adress och nyckel? Skriv ja: " SURE
  [ "$SURE" = "ja" ] || { echo "Avbrutet, inget andrat"; exit 0; }
fi
echo "API Base URL star pa startsidan i Infobips portal, till exempel xxxxx.api.infobip.com"
read -r -p "API Base URL: " HOST
HOST="${HOST#https://}"; HOST="${HOST%/}"
[[ "$HOST" =~ ^([a-z0-9-]+\.)?api\.infobip\.com$ ]] || { echo "Det ar inte en Infobip-adress"; exit 1; }
read -r -s -p "API-nyckel (visas inte): " KEY; echo
test -n "$KEY" || { echo "Nyckel kravs"; exit 1; }

# The key is passed to curl and jq through stdin and the environment, so it
# never shows up in the process list.
STATUS="$(printf 'header = "Authorization: App %s"\n' "$KEY" \
  | curl -s -o /dev/null -w '%{http_code}' -K - "https://${HOST}/account/1/balance")"
echo "Infobip svarade $STATUS pa en provfraga."
if [ "$STATUS" = "401" ]; then echo "Nyckeln godkandes inte. Inget sparat."; exit 1; fi

printf %s "$CURRENT" | KEY="$KEY" jq -c --arg a "$ALIAS" --arg h "$HOST" \
  '.[$a] = {base_url: $h, api_key: env.KEY}' \
  | gcloud secrets versions add "$ACCOUNTS_ID" --data-file=- >/dev/null
unset KEY

# A new revision makes the running service read the new list.
gcloud run services update "$SERVICE" --region="$REGION" \
  --update-labels="accounts-updated=$(date +%s)" --quiet >/dev/null
echo "KLART. Kontot '$ALIAS' finns nu i servern."

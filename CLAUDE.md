# Messaging MCP

Egen MCP-server (Python, FastMCP) för mejl, SMS och RCS genom Infobips API,
driftsatt på Cloud Run. Läs `SETUP.md` för adresser, verktyg och aktuell status.

## Namngivning

- Döp ingenting efter byrån eller en kund: inga filer, grenar, verktyg,
  variabler, miljövariabler, kommentarer eller testdata. Använd namn som
  beskriver vad saken gör.
- Undantag som inte går att ändra: Google Cloud-projektets ID. Det får stå i
  driftskripten och i `SETUP.md`.
- Kontonas kortnamn väljs av användaren i `deploy/add-account.sh` och finns
  bara i Secret Manager. Skriv aldrig ett riktigt kortnamn i repot.
- Kod, kommentarer och commit-meddelanden skrivs på engelska. `SETUP.md` och
  den här filen skrivs på svenska.

## Arbetsgång

1. Skapa en gren från `main` med ett beskrivande namn.
2. Gör ändringen och skriv tester i `tests/` (verktyg i `tests/tools/`).
3. Kör `python -m pytest tests`. Allt ska gå igenom.
4. Slå ihop med `main` och ta bort grenen.
5. Rulla ut med `bash deploy/deploy.sh` i Cloud Shell.
6. Uppdatera verktygslistan i Claude: öppna connectorn och välj
   "Refresh tools list". Testa i en ny chatt.

## Regler för verktyg som ändrar eller skickar något

- Använd `messaging_mcp/confirm.py`: första anropet läser nuläget och ger en
  kontrollsumma, andra anropet genomför. `validate_only` är förvalt `True`.
- Slå upp och visa namn och nuvarande värde innan något ändras.
- Inget verktyg får ta bort mallar, personer, taggar, segment eller annat.
- Inget skickas till en målgrupp utan ett testutskick av exakt samma
  innehåll först (`sending.require_test_token`).
- Förslaget för ett utskick ska visa antal mottagare, avsändare, innehåll och
  sändningstid. Kontrollsumman ska bindas till målgruppens fingeravtryck.
- Varje anrop mot Infobip går genom `api.request`, som kontrollerar att den
  inloggade står i `MESSAGING_MCP_ALLOWED_EMAILS`.
- Varje verktyg tar `account` som första argument. Gissa aldrig konto.
- Nya verktyg läggs i `messaging_mcp/tools/` och importeras i `server.py`.
  Lägg också till namnet i listan i `tests/core_test.py`.

## Bygg inte om Infobips egna servrar

Infobip har egna MCP-servrar (`https://mcp.infobip.com/...`). Det som de gör
bra läggs till direkt i Claude. Här byggs bara det som saknas där, eller som
kräver flera konton eller tvåstegsbekräftelse.

## Hemligheter

Inga hemligheter i repot. De ligger i Secret Manager och klistras in av
användaren vid en dold fråga i Cloud Shell. API-nycklar får aldrig hamna i
felmeddelanden, loggar eller verktygssvar.

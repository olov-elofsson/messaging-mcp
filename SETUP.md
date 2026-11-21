# Messaging MCP: uppsättning och drift

Egen MCP-server för mejl, SMS och RCS genom Infobips API. Den kompletterar
Infobips egna MCP-servrar och gör det som de saknar: mejlmallar, utskick till
en tagg eller ett segment med test och bekräftelse, och flera konton.

## Vad som körs

| Del | Värde |
|---|---|
| Google Cloud-projekt | `wigo-ads-mcp` (nummer 17668607738) |
| Cloud Run-tjänst | `messaging-mcp`, region `europe-north1`, max 1 instans |
| MCP-adress | `https://messaging-mcp-17668607738.europe-north1.run.app/mcp` |
| Redirect-URI | `https://messaging-mcp-17668607738.europe-north1.run.app/auth/callback` |
| Hemligheter | Secret Manager: `messaging-oauth-client-secret`, `messaging-jwt-signing-key`, `messaging-accounts` |
| Sessionslagring | Firestore-databasen `messaging-sessions`, plats `eur3` |
| Körkonto | `messaging-mcp-run@wigo-ads-mcp.iam.gserviceaccount.com` (eget, når bara de tre hemligheterna ovan) |

Inga hemligheter ligger i det här repot. Tjänsten är inte driftsatt än.

## Driftsätta första gången

1. Skapa en OAuth-klient i Google Cloud: Google Auth Platform > Clients >
   Create client > Web application. Lägg in redirect-URI:n från tabellen ovan.
2. Kör i Cloud Shell, från repots rot:

   ```
   bash deploy/deploy.sh
   ```

   Skriptet frågar efter Client ID, Client secret (dold fråga) och vilka
   e-postadresser som får logga in. Bara de adresserna kan använda servern,
   även om någon annan lyckas logga in med Google.
3. Lägg till ett Infobip-konto (upprepa per konto):

   ```
   bash deploy/add-account.sh
   ```

   Skriptet frågar efter ett kortnamn, kontots API Base URL och API-nyckeln
   (dold fråga). Kortnamnet är det du sedan säger i Claude för att välja
   konto. Det sparas bara i Secret Manager, aldrig i koden.
4. Lägg till servern i Claude som en egen connector med MCP-adressen ovan
   och logga in med Google.

Rulla ut ny kod senare: kör `bash deploy/deploy.sh` igen. Då frågar det inte
efter något.

### Behörigheter på API-nyckeln i Infobip

Skapa nyckeln i Infobips portal under Developers > API keys med dessa
scopes: `email:manage`, `sms:manage`, `people:read`, `message:send`
(för RCS) och den som gäller Moments-flöden om flödesverktyget ska användas.
Namnen på scopes är inte kontrollerade mot ett riktigt konto. Saknas en
behörighet svarar Infobip 403 och verktyget visar det.

### Byta vilka som får logga in

```
gcloud run services update messaging-mcp --region=europe-north1 \
  --update-env-vars="^##^MESSAGING_MCP_ALLOWED_EMAILS=a@example.com,b@example.com"
```

## Infobips egna servrar som används bredvid

Läggs till direkt i Claude som connectors och loggas in med Infobip-kontot.
En sådan connector gäller ett Infobip-konto i taget.

| Server | Adress | Används till |
|---|---|---|
| People | `https://mcp.infobip.com/people` | Profiler, taggar, listor, segment |
| Account Management | `https://mcp.infobip.com/account-management` | Saldo, kontouppgifter |
| Documentation | `https://mcp.infobip.com/search` | Sök i Infobips dokumentation |

## Verktyg

Verktyg märkta "två steg" gör ingenting vid första anropet. De svarar med en
sammanfattning och en kontrollsumma. Först ett andra anrop med kontrollsumman
genomför saken, och det nekas om något har ändrats emellan.

| Verktyg | Gör | Spärrar |
|---|---|---|
| `accounts_list` | Listar kontonas kortnamn | Visar aldrig nycklar |
| `templates_list`, `templates_get` | Listar och hämtar mejlmallar | Bara läsning |
| `templates_create` | Skapar en mall från HTML | Två steg. Nekar namn som redan finns. Varnar om avregistreringslänk saknas |
| `templates_update` | Ändrar namn, HTML, ämne, preheader, avsändare, svarsadress | Två steg. Visar nuvarande och nytt värde. Ändrar bara det som skickas in |
| `templates_copy` | Kopierar en mall, även till ett annat konto | Två steg. Originalet rörs inte. Avsändare följer inte med till ett annat konto. Bilagor kopieras inte |
| `audience_list_tags`, `audience_list_segments`, `audience_preview` | Visar taggar och segment i People och hur många ett utskick skulle nå | Bara läsning |
| `email_send_test` | Testmejl med en mall till högst fem adresser | Två steg. Ämnet får prefixet [TEST] |
| `email_send_campaign` | Mall till en tagg eller ett segment, direkt eller schemalagt | Två steg. Kräver ett testutskick av samma mall, avsändare och ämne. Nekas om målgruppen ändrats sedan förslaget |
| `text_preview` | Visar SMS-texten som den levereras och antal SMS-delar | Skickar inget |
| `text_send_test` | Test-SMS eller test-RCS till högst fem nummer | Två steg |
| `text_send_campaign` | SMS eller RCS till en tagg eller ett segment | Två steg. Kräver test av samma text och avsändare. Visar mottagare och antal SMS-delar |
| `scheduled_get_status` | Status för ett schemalagt utskick (mejl, SMS) | Bara läsning |
| `scheduled_cancel` | Avbryter ett schemalagt utskick som inte gått ut | Två steg. Visar nuvarande status |
| `reports_delivery` | Levererat, väntar och misslyckat per utskick, med orsaker och kostnad | Bara läsning |
| `flows_add_participants` | Lägger till personer i ett aktivt Moments-flöde | Två steg. Högst 1000 per anrop |
| `flows_get_add_report` | Visar om personerna togs emot | Bara läsning |

Inget verktyg tar bort något: inga mallar, personer, taggar eller segment.

Gränser: högst 20 000 mottagare per utskick (miljövariabeln
`MESSAGING_MCP_MAX_RECIPIENTS`), schemaläggning högst 30 dagar framåt,
utskick delas i omgångar om 500. Går en omgång fel stannar utskicket och
felet säger vilka omgångar Infobip redan tagit emot.

"Lista" i People: Infobip har ingen vanlig mottagarlista. En fast grupp
personer görs med en tagg, en regelstyrd grupp med ett segment.

## Det här går inte, eller är inte byggt

- **Flöden i Moments** kan inte skapas, ändras, startas eller listas via
  Infobips API. Det enda som går är att lägga till personer i ett flöde som
  redan är aktivt och har startpunkten Flow API. Flödets ID står i
  adressfältet när flödet är öppet i Infobip.
- **Öppningar, klick och avregistreringar** finns inte i de loggar som
  `reports_delivery` läser. Infobip lämnar dem bara genom sitt Metrics API,
  som skickar svaret till en adress hos mottagaren i stället för att svara
  direkt. Det är inte byggt. Siffrorna finns i Infobip under Analyze.
- **Pris i förväg**: Infobip har ingen prisfråga som har gått att hitta.
  Förslaget visar antal mottagare och SMS-delar. Kostnaden visas efteråt av
  `reports_delivery`.
- **RCS**: bara textmeddelanden. Kort, karuseller och knappar är inte byggda.
  Status och rapport för RCS finns inte i `scheduled_*` och `reports_delivery`.
- **Loggar** sparas en begränsad tid hos Infobip (ungefär 48 timmar), så
  rapporten ska hämtas kort efter utskicket.

## Status 2026-11-21

- All kod har tester som körs mot ett låtsas-API. Alla går igenom.
- Inget är provat mot ett riktigt Infobip-konto och tjänsten är inte driftsatt.
- Adresser och fältnamn kommer från Infobips officiella Python-klient och
  OpenAPI-specifikation. Tre saker är inte bekräftade där och kan behöva
  justeras vid första riktiga körningen: filtret för personer med en viss
  tagg (`{"#contains": {"tags": ...}}`), formen på svaret från
  `/people/3/segments` och `/people/3/segments/{id}/persons`, samt att
  `PATCH` på en mall godtar enstaka fält.

Tester: `python -m pytest tests`

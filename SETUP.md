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

| OAuth | Klienten "Messaging MCP server" (Web application) |

Inga hemligheter ligger i det här repot.

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

Skapa nyckeln i Infobips portal under Developer Tools > API Keys med dessa
scopes (provade mot ett riktigt konto 2026-11-21):

`email:manage`, `email:templates:manage`, `sms:manage`,
`messages-api:manage`, `flow:manage`, `flow:read`, `people:manage`,
`people:read`

`people:read` behövs utöver `people:manage`, annars svarar Infobip 403 när
personerna i en tagg ska läsas. Skriptet `add-account.sh` gör en provfråga
om saldo som svarar 403 med de här behörigheterna. Det är väntat och betyder
bara att nyckeln inte får läsa saldot.

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
| `rcs_preview` | Kontrollerar ett rikt RCS-meddelande (text med knappar, kort, karusell) | Skickar inget |
| `rcs_send_test` | Testar ett rikt RCS-meddelande mot högst fem nummer | Två steg |
| `rcs_send_campaign` | Rikt RCS-meddelande till en tagg eller ett segment, med SMS som reserv | Två steg. Kräver test av exakt samma meddelande, avsändare och reservtext |
| `rcs_check_capability` | Räknar hur många i en målgrupp som kan ta emot RCS | Bara läsning |
| `email_list_sender_domains` | Listar domäner som kontot får skicka mejl från | Bara läsning |
| `email_list_suppressions` | Listar adresser som Infobip spärrat (studs, klagomål, kvot) | Bara läsning |
| `scheduled_get_status` | Status för ett schemalagt utskick (mejl, SMS) | Bara läsning |
| `scheduled_reschedule` | Flyttar ett schemalagt utskick till en annan tid (mejl, SMS) | Två steg. Visar nuvarande tid |
| `scheduled_cancel` | Avbryter ett schemalagt utskick som inte gått ut | Två steg. Visar nuvarande status |
| `reports_delivery` | Levererat, väntar och misslyckat per utskick, med orsaker och kostnad (mejl, SMS, RCS) | Bara läsning |
| `reports_statistics` | Levererat, öppnat, klickat, studsar och avregistreringar för en period, per utskick, dag, månad, avsändare, status, flöde eller länk | Bara läsning |
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

- **Flöden (journeys) i Moments** kan inte skapas, byggas, ändras, startas
  eller listas via Infobips publika API. Det är kontrollerat mot hela
  API-specifikationen (690 adresser, 2026-11-21): det enda som finns är att
  lägga till och ta bort personer i ett flöde som redan är aktivt och har
  startpunkten Flow API. Portalen har behörigheten `flow:control`, men ingen
  publik adress använder den. Flöden byggs alltså i Infobips webbgränssnitt.
  Flödets ID står i adressfältet när flödet är öppet.
  En enkel serie utan villkor (till exempel mejl dag 0 och SMS dag 3 till
  samma målgrupp) går att göra som flera schemalagda utskick.
- **Statistiken** hämtas från Infobips Metrics API, som skickar svaret till
  serverns adress `/metrics-callback/<engångskod>`. Bara summerade tal går
  den vägen och inget sparas. Siffrorna kan släpa några minuter, och en
  period får vara högst 93 dagar.
- **Pris i förväg**: Infobip har ingen prisfråga. Förslaget visar antal
  mottagare och SMS-delar. Kostnaden visas efteråt av `reports_delivery`.
- **RCS**: status och flytt av schemalagda RCS-utskick finns inte
  (`scheduled_*` gäller mejl och SMS).
- **Personliga texter i SMS och RCS** är inte byggt. Alla får samma text.
- **Loggar** sparas en begränsad tid hos Infobip (ungefär 48 timmar), så
  `reports_delivery` ska köras kort efter utskicket. `reports_statistics`
  går längre bakåt.

## Status 2026-11-21

- All kod har tester som körs mot ett låtsas-API. Alla går igenom.
- Tjänsten är driftsatt och svarar. Inloggning är begränsad till en adress.
- Servern är tillagd i Claude som connectorn "Messaging (Infobip)".
- Provat mot ett riktigt konto: `accounts_list`, `templates_list`,
  `templates_get`, `templates_create`, `audience_list_tags`,
  `audience_preview` för en tagg och `text_preview`. Inget utskicksverktyg
  är provat på riktigt.
- Adresser och fältnamn kommer från Infobips OpenAPI-specifikation. Inte
  bekräftat mot ett riktigt konto: att `PATCH` på en mall godtar enstaka
  fält, och alla utskicksverktyg.

Tester: `python -m pytest tests`

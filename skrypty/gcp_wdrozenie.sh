#!/usr/bin/env bash
# Wdrożenie usługi ksef-connector (panel BOK) w Cloud Run. Uruchamia administrator projektu GCP.
#
# Wymagane zmienne środowiskowe:
#   PROJEKT        projekt GCP z usługą i sekretami
#   PROJEKT_BQ     projekt BigQuery z danymi ERP (może być ten sam)
#   ZBIOR_ERP      zbiór BigQuery z widokiem v_ksef_dokumenty (domyślnie erp_xl)
#   SA_APLIKACJI   konto usługi aplikacji, która może wywoływać usługę (frontend panelu)
#   KSEF_NIP       NIP firmy (kontekst KSeF)
# Plik firma.toml (dane sprzedawcy i rachunki) trafia do usługi jako sekret ksef-firma (patrz niżej).
# Można uruchamiać wielokrotnie (istniejące konto / sekret / uprawnienia tylko zgłoszą „already exists”).
#
#   bash skrypty/gcp_wdrozenie.sh          # konto usługi, uprawnienia, pusty sekret, wdrożenie bez tokenu
#   bash skrypty/gcp_wdrozenie.sh token    # po dodaniu tokenu do sekretu: podpięcie go do usługi
#
# Token KSeF NIE jest tu podawany. Dodanie (wklej token, Enter, Ctrl-D — nie zostaje w historii powłoki):
#   gcloud secrets versions add ksef-token-prod --project="$PROJEKT" --data-file=-
#   gcloud secrets create ksef-firma --project="$PROJEKT" --data-file=firma.toml
set -euo pipefail

: "${PROJEKT:?ustaw PROJEKT}" "${PROJEKT_BQ:?ustaw PROJEKT_BQ}" "${SA_APLIKACJI:?ustaw SA_APLIKACJI}" "${KSEF_NIP:?ustaw KSEF_NIP}"
ZBIOR_ERP=${ZBIOR_ERP:-erp_xl}
REGION=${REGION:-europe-central2}
USLUGA=ksef-connector
SA=ksef-connector-run@${PROJEKT}.iam.gserviceaccount.com
KATALOG="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${1:-}" == "token" ]]; then
  gcloud run services update "$USLUGA" --project="$PROJEKT" --region="$REGION" \
    --update-secrets=KSEF_TOKEN_PROD=ksef-token-prod:latest
  exit 0
fi

echo "== Konto usługi"
gcloud iam service-accounts create ksef-connector-run --project="$PROJEKT" \
  --display-name="ksef-connector (panel BOK)" \
  --description="Usługa KSeF: odczyt ${ZBIOR_ERP}.v_ksef_dokumenty, zapis zbioru ksef, token KSeF z Secret Manager" || true

echo "== BigQuery ($PROJEKT_BQ): zadania, odczyt TYLKO widoku KSeF, zapis zbioru ksef"
gcloud projects add-iam-policy-binding "$PROJEKT_BQ" --member="serviceAccount:$SA" \
  --role=roles/bigquery.jobUser --condition=None --format=none
bq query --project_id="$PROJEKT_BQ" --location="$REGION" --use_legacy_sql=false --format=none \
  "GRANT \`roles/bigquery.dataViewer\` ON TABLE \`${PROJEKT_BQ}.${ZBIOR_ERP}.v_ksef_dokumenty\` TO \"serviceAccount:$SA\";
   GRANT \`roles/bigquery.dataEditor\` ON SCHEMA \`${PROJEKT_BQ}.ksef\` TO \"serviceAccount:$SA\""

echo "== Sekret na token KSeF (pusty; token dodaje użytkownik)"
gcloud secrets create ksef-token-prod --project="$PROJEKT" --replication-policy=user-managed \
  --locations="$REGION" --labels=app=ksef-connector || true
for SEKRET in ksef-token-prod ksef-firma; do
  gcloud secrets add-iam-policy-binding "$SEKRET" --project="$PROJEKT" --member="serviceAccount:$SA" \
    --role=roles/secretmanager.secretAccessor --format=none
done

echo "== Wdrożenie (prywatne, jedna instancja — wysyłki po kolei)"
gcloud run deploy "$USLUGA" --source="$KATALOG" --project="$PROJEKT" --region="$REGION" \
  --service-account="$SA" --no-allow-unauthenticated \
  --max-instances=1 --concurrency=8 --timeout=900 --memory=512Mi \
  --set-env-vars=KSEF_SRODOWISKO=prod,KSEF_NIP="$KSEF_NIP",KSEF_BQ_PROJEKT="$PROJEKT_BQ",KSEF_BQ_ZBIOR_ERP="$ZBIOR_ERP",KSEF_FIRMA=/secrets/firma.toml \
  --set-secrets=/secrets/firma.toml=ksef-firma:latest

echo "== Wywoływać może tylko aplikacja z panelem ($SA_APLIKACJI)"
gcloud run services add-iam-policy-binding "$USLUGA" --project="$PROJEKT" --region="$REGION" \
  --member="serviceAccount:$SA_APLIKACJI" --role=roles/run.invoker --format=none

gcloud run services describe "$USLUGA" --project="$PROJEKT" --region="$REGION" --format="value(status.url)"

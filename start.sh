#!/bin/bash
# Railway startup script
# Decodifica o JSON em Base64 para /tmp (evita problemas com caracteres especiais)
echo "$GOOGLE_CREDENTIALS_B64" | base64 -d > /tmp/Arquivo_Json_AutomacaoPlanilhaBruno.json
# Executa o script principal
exec python -u scripts/maio_26.py

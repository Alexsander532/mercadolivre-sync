#!/bin/bash
# Railway startup script
# Escreve o JSON de credenciais do Google a partir da variável de ambiente
echo "$GOOGLE_CREDENTIALS_JSON" > /tmp/Arquivo_Json_AutomacaoPlanilhaBruno.json
# Executa o script principal
exec python -u scripts/maio_26.py

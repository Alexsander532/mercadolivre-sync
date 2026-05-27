#!/bin/bash
# Railway startup script
# Escreve o JSON de credenciais do Google a partir da variável de ambiente
python -c "
import os, sys
content = os.environ.get('GOOGLE_CREDENTIALS_JSON')
if not content:
    print('ERRO: GOOGLE_CREDENTIALS_JSON nao definida')
    sys.exit(1)
with open('/tmp/Arquivo_Json_AutomacaoPlanilhaBruno.json', 'w') as f:
    f.write(content)
print('JSON escrito com sucesso')
"
# Executa o script principal
exec python -u scripts/maio_26.py

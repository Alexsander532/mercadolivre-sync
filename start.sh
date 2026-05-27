#!/bin/bash
# Railway startup script
set -e

echo "=== DIAGNÓSTICO ==="
echo "B64 var length: ${#GOOGLE_CREDENTIALS_B64}"

# Decodifica usando Python (mais confiável)
python3 -c "
import base64, sys, os
b64 = os.environ.get('GOOGLE_CREDENTIALS_B64', '')
print(f'B64 var length: {len(b64)}')
if not b64:
    print('ERRO: GOOGLE_CREDENTIALS_B64 nao definida!')
    sys.exit(1)
try:
    decoded = base64.b64decode(b64).decode('utf-8')
    with open('/tmp/Arquivo_Json_AutomacaoPlanilhaBruno.json', 'w') as f:
        f.write(decoded)
    print(f'JSON decodificado: {len(decoded)} bytes')
    import json
    json.loads(decoded)
    print('JSON valido!')
except Exception as e:
    print(f'ERRO: {e}')
    sys.exit(1)
"
echo "=== FIM DIAGNÓSTICO ==="

# Executa o script principal
exec python -u scripts/maio_26.py

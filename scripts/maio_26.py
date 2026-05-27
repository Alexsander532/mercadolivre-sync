import requests
from datetime import datetime, timezone
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import os
import schedule  # Adiciona o schedule
from dotenv import load_dotenv  # Para carregar variáveis de ambiente

# Carregar variáveis de ambiente do arquivo .env (se existir)
load_dotenv()

# Função para obter timestamp formatado para logs
def obter_timestamp():
    return datetime.now().strftime("[%d/%m/%Y %H:%M:%S]")

# Função para atualizar o access token
def atualizar_access_token(client_id, client_secret, refresh_token):
    url = "https://api.mercadolibre.com/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token
    }

    response = requests.post(url, data=payload)

    if response.status_code == 200:
        data = response.json()
        return data['access_token']
    else:
        print(f"{obter_timestamp()} Erro ao atualizar o Access Token: {response.status_code}, {response.text}")
        return None

# Função para requisitar detalhes do envio
def obter_detalhes_envio(shipment_id, access_token):
    url = f"https://api.mercadolibre.com/shipments/{shipment_id}"
    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        logistic_type = data.get('logistic_type', 'Tipo de envio não encontrado')
        return logistic_type
    else:
        print(f"{obter_timestamp()} Erro ao obter detalhes do envio {shipment_id}: {response.status_code}, {response.text}")
        return 'Tipo de envio não encontrado'

# Função para requisitar os pedidos do Mercado Livre
def requisitar_pedidos(access_token, seller_id, date_from, date_to):
    url_base = f"https://api.mercadolibre.com/orders/search/recent?seller={seller_id}"
    headers = {"Authorization": f"Bearer {access_token}"}

    offset = 0
    limit = 50  # Número máximo de resultados por requisição
    orders = []

    while True:
        url = f"{url_base}&date_created_from={date_from}&date_created_to={date_to}&offset={offset}&limit={limit}"
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            current_orders = data.get('results', [])

            if not current_orders:
                break

            orders.extend(current_orders)
            offset += limit
        else:
            print(f"{obter_timestamp()} Erro: {response.status_code}, Mensagem: {response.text}")
            break

    return orders

# Função para obter IDs de pedidos já existentes na planilha
def obter_ids_existentes(aba):
    ids_existentes = aba.col_values(2)  # Coluna B contém os IDs dos pedidos
    return set(str(id).strip() for id in ids_existentes[1:])  # Ignora o cabeçalho e converte em um set

# Função para filtrar pedidos PAID e de novembro de 2024
def filtrar_pedidos_paid_fevereiro(pedidos):
    pedidos_filtrados = []
    for pedido in pedidos:
        if pedido.get('status') == 'paid':  # Verifica se o status é 'paid'
            date_created = pedido.get('date_created', '')
            try:
                dt = datetime.strptime(date_created[:-6], "%Y-%m-%dT%H:%M:%S.%f")
                if dt.year == 2026 and dt.month == 5:
                    pedidos_filtrados.append(pedido)
            except Exception as e:
                print(f"{obter_timestamp()} Erro ao processar a data do pedido {pedido.get('id')}: {e}")
    return pedidos_filtrados

# Cache para armazenar o valor dos SKUs temporariamente
sku_cache = {}

# Função para consultar o valor do SKU e calcular o valor comprado
def consultar_valor_sku_e_calcular(aba_dados, sku, quantity):
    global sku_cache

    if sku in sku_cache:
        return sku_cache[sku] * quantity

    dados = {row[0]: float(row[1].replace('R$', '').replace('.', '').replace(',', '.').strip()) for row in
             aba_dados.get_all_values()[1:] if row}
    sku_cache.update(dados)

    return dados.get(sku, 0) * quantity

# Função para calcular o valor líquido, ajustando para quantidade
def calcular_valor_liquido(valor_vendido, taxes, frete, ctl, quantidade):
    valor_vendido_total = valor_vendido * quantidade
    taxes_total = taxes * quantidade
    frete_total = frete
    ctl_total = ctl

    if valor_vendido_total > 79.00:
        valor_liquido = valor_vendido_total - taxes_total - frete_total - ctl_total - (valor_vendido_total * 0.0741)
        print(f"{obter_timestamp()} Cálculo Valor Líquido: Valor Vendido (R$ {valor_vendido_total:.2f}) - Taxas (R$ {taxes_total:.2f}) - Frete (R$ {frete_total:.2f}) - CTL (R$ {ctl_total:.2f}) - Comissão (R$ {valor_vendido_total * 0.0741:.2f}) = R$ {valor_liquido:.2f}")
    else:
        valor_liquido = valor_vendido_total - taxes_total - ctl_total - (valor_vendido_total * 0.0741)
        print(f"{obter_timestamp()} Cálculo Valor Líquido: Valor Vendido (R$ {valor_vendido_total:.2f}) - Taxas (R$ {taxes_total:.2f}) - CTL (R$ {ctl_total:.2f}) - Comissão (R$ {valor_vendido_total * 0.0741:.2f}) = R$ {valor_liquido:.2f}")

    return valor_liquido

# Função para calcular o imposto (9,2%)
def calcular_imposto(valor_vendido_total):
    return valor_vendido_total * 0.1

# Função para calcular o lucro
def calcular_lucro(valor_liquido, valor_comprado):
    return valor_liquido - valor_comprado

# Função para calcular o MARKUP
def calcular_markup(lucro, valor_comprado):
    if valor_comprado > 0:
        return (lucro * 100) / valor_comprado  # Cálculo do MARKUP em porcentagem
    else:
        return 0.0

# Função para calcular a Margem de Lucro
def calcular_margem_lucro(lucro, valor_vendido):
    if valor_vendido > 0:
        return (lucro * 100) / valor_vendido  # Cálculo da Margem de Lucro em porcentagem
    else:
        return 0.0

# Função para exibir detalhes do pedido no console de maneira clara
def exibir_detalhes_pedido(order_id, formatted_date, sku, quantity, unit_price, taxes, frete, logistic_type,
                           ctl, liquid_value, imposto, profit, valor_comprado, markup, margem_lucro, shipment_id):
    print(f"\n{obter_timestamp()} --- Detalhes do Pedido {order_id} ---")
    print(f"{obter_timestamp()} Data: {formatted_date}")
    print(f"{obter_timestamp()} SKU: {sku}")
    print(f"{obter_timestamp()} Quantidade: {quantity}")
    print(f"{obter_timestamp()} Valor Unitário: R$ {unit_price * quantity:.2f}")
    print(f"{obter_timestamp()} Taxas: R$ {taxes:.2f}")
    print(f"{obter_timestamp()} Frete: R$ {frete:.2f}")
    print(f"{obter_timestamp()} Tipo de Envio: {logistic_type}")
    print(f"{obter_timestamp()} CTL: R$ {ctl:.2f}")
    print(f"{obter_timestamp()} Imposto: R$ {imposto:.2f}")
    print(f"{obter_timestamp()} Valor Líquido: R$ {liquid_value:.2f}")
    print(f"{obter_timestamp()} Lucro: R$ {profit:.2f} (Cálculo: R$ {liquid_value:.2f} - R$ {valor_comprado:.2f})")
    print(f"{obter_timestamp()} MARKUP: {markup:.2f}% (Cálculo: ((R$ {profit:.2f} * 100) / R$ {valor_comprado:.2f}))")
    print(f"{obter_timestamp()} Margem de Lucro: {margem_lucro:.2f}% (Cálculo: ((R$ {profit:.2f} * 100) / R$ {unit_price * quantity:.2f}))")
    print(f"{obter_timestamp()} Este é o chipment_id = {shipment_id}")
    if (unit_price * quantity) < 79.00:
        print(f"{obter_timestamp()} Frete não conta. Valor vendido de R${(unit_price * quantity):.2f} abaixo de R$ 79,00")
    print(f"{obter_timestamp()} ----------------------------\n")

# Função para inserir os dados na planilha do Google Sheets
def inserir_dados_planilha(orders, aba, ids_existentes, aba_dados, access_token):
    dados_a_inserir = []

    for order in orders:
        order_id = str(order.get('id', 'ID não encontrado')).strip()

        # Verificar se o pedido já existe na planilha
        if order_id in ids_existentes:
            print(f"{obter_timestamp()} Pedido {order_id} já existe na planilha, pulando.")
            continue

        date_created = order.get('date_created', 'Data não encontrada')

        try:
            dt_str = date_created[:-6]
            dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S.%f")
            formatted_date = dt.strftime("%d/%m/%y %H:%M:%S")
        except Exception as e:
            formatted_date = 'Data inválida'
            print(f"{obter_timestamp()} Erro ao processar a data: {e}")

        items = order.get('order_items', [])
        if items:
            sku = items[0].get('item', {}).get('seller_sku', 'SKU não encontrado')
            quantity = int(items[0].get('quantity', 0) or 0)
            unit_price = float(items[0].get('unit_price', 0) or 0)
            taxes = float(items[0].get('sale_fee', 0) or 0)
        else:
            sku = 'SKU não encontrado'
            quantity = 0
            unit_price = 0
            taxes = 0

        #payments = order.get('payments', [])
        #if payments:
            #shipping_cost = payments[0].get('shipping_cost', 0)
        #else:
            #shipping_cost = 0

        # Consultar o tipo de envio usando o shipment_idyy
        shipment_id = order.get('shipping', {}).get('id', '')
        logistic_type = obter_detalhes_envio(shipment_id, access_token)



        #Calcular corretamente o frete
        # Função para obter o valor correto do frete a partir do campo "save" em "senders"
        def obter_frete(shipment_id, access_token):
            url = f"https://api.mercadolibre.com/shipments/{shipment_id}/costs"
            headers = {"Authorization": f"Bearer {access_token}"}

            response = requests.get(url, headers=headers)

            if response.status_code == 200:
                data = response.json()
                shipping_cost = data.get("senders", [{}])[0].get("save", 0)  # Pega o valor de 'save' em 'senders'
            else:
                shipping_cost = 0
                print(f"{obter_timestamp()} Erro ao obter custo de envio {shipment_id}: {response.status_code}, {response.text}")

            return shipping_cost

        frete = obter_frete(shipment_id, access_token)

        # Definir colunas R e S com base no tipo de envio e calcular CTL
        if logistic_type == 'fulfillment':
            tipo_envio = 'FULL'
            tipo_envio_num = 1
            ctl = 1.50 * quantity # CTL multiplicado pela quantidade apenas para 'FULL'
        elif logistic_type == 'self_service':
            tipo_envio = 'FLEX'
            tipo_envio_num = 2
            ctl = 6.50
            frete = 13.68
        elif logistic_type == 'cross_docking':
            tipo_envio = 'COLETAGEM'
            tipo_envio_num = 3
            ctl = 6.50
        else:
            tipo_envio = 'DESCONHECIDO'
            tipo_envio_num = 0
            ctl = 0.00

        # Consultar o valor comprado
        valor_comprado = consultar_valor_sku_e_calcular(aba_dados, sku, quantity)

        # Calcular o valor líquido, ajustado para quantidade
        valor_liquido = calcular_valor_liquido(unit_price, taxes, frete, ctl, quantity)

        # Calcular o imposto (9,2%)
        imposto = calcular_imposto(unit_price * quantity)

        # Calcular o lucro
        lucro = calcular_lucro(valor_liquido, valor_comprado)

        # Calcular o MARKUP
        markup = calcular_markup(lucro, valor_comprado)

        # Calcular a Margem de Lucro
        margem_lucro = calcular_margem_lucro(lucro, unit_price * quantity)

        # Exibir detalhes do pedido no console com valores de cálculo
        exibir_detalhes_pedido(order_id, formatted_date, sku, quantity, unit_price, taxes, frete, logistic_type,
                               ctl, valor_liquido, imposto, lucro, valor_comprado, markup, margem_lucro, shipment_id)

        # Adicionando os dados à lista, incluindo o imposto na coluna T
        dados_a_inserir.append([
            'MERCADO LIVRE',  # Coluna A
            order_id,  # Coluna B
            formatted_date,  # Coluna C
            sku,  # Coluna D
            quantity,  # Coluna E
            order.get('status', '').upper(),  # Coluna F
            f"R$ {valor_comprado:.2f}",  # Coluna G (Valor Comprado)
            f"R$ {unit_price*quantity:.2f}",  # Coluna H (Valor Vendido)
            f"R$ {taxes*quantity:.2f}",  # Coluna I (Taxas)
            f"R$ {frete:.2f}",  # Coluna J (Frete)
            '',  # Coluna K (Desconto será manual)
            f"R$ {ctl:.2f}",  # Coluna L (CTL)
            '',  # Coluna M (Receita de envio será manual)
            f"R$ {valor_liquido:.2f}",  # Coluna N (Valor Líquido)
            f"R$ {lucro:.2f}",  # Coluna O (Lucro)
            f"{markup:.2f}%",  # Coluna P (MARKUP)
            f"{margem_lucro:.2f}%",  # Coluna Q (Margem de Lucro)
            tipo_envio,  # Coluna R
            tipo_envio_num,  # Coluna S
            f" R$ {imposto:.2f}" # Coluna T (Imposto)
        ])

    if dados_a_inserir:
        aba.insert_rows(dados_a_inserir, 2)

# Função para organizar a planilha mantendo a linha 1 intacta e reorganizando todas as colunas
def organizar_planilha(aba):
    data = aba.get_all_values()

    if len(data) <= 1:
        return  # Não faz nada se não houver dados além do cabeçalho

    # Extrair apenas os dados das colunas A a T (com todas as linhas, mesmo sem data)
    dados_colunas_A_T = [linha[:20] for linha in data[1:] if len(linha) > 0]  # Pega todas as linhas não vazias

    # Separar dados com data válida para ordenar
    dados_com_data = []
    dados_sem_data = []
    
    for linha in dados_colunas_A_T:
        try:
            if len(linha) > 2 and linha[2].strip():  # Se tem data na coluna C
                datetime.strptime(linha[2], "%d/%m/%y %H:%M:%S")
                dados_com_data.append(linha)
            else:
                dados_sem_data.append(linha)
        except ValueError:
            # Data inválida, mantém na lista sem data
            dados_sem_data.append(linha)

    # Organizar apenas as linhas com data válida, em ordem descendente
    dados_organizados = sorted(dados_com_data, key=lambda x: datetime.strptime(x[2], "%d/%m/%y %H:%M:%S"),
                               reverse=True)
    
    # Combinar: dados com data (ordenados) + dados sem data (preservados)
    dados_finais = dados_organizados + dados_sem_data

    # Limpar as células de A2 até T da última linha
    intervalo_limpeza = f"A2:T{len(data)}"
    aba.batch_clear([intervalo_limpeza])

    # Atualizar a planilha de uma vez só com os dados organizados (incluindo linhas sem data)
    if dados_finais:
        range_update = f"A2:T{len(dados_finais) + 1}"
        aba.update(range_name=range_update, values=dados_finais)

# Função para calcular e adicionar as somas nas colunas especificadas, duas linhas abaixo da última linha de dados
def adicionar_somas_planilha(aba):
    valores = aba.get_all_values()  # Pega todos os valores da planilha
    
    # Primeiro, deletar a linha de soma anterior (se existir)
    for i, linha in enumerate(valores):
        if len(linha) > 3 and 'Produtos Vendidos:' in linha[3]:  # Coluna D tem índice 3
            print(f"{obter_timestamp()} Deletando linha de soma anterior (linha {i + 1})...")
            aba.delete_rows(i + 1)  # delete_rows usa números de linha (1-indexed)
            break
    
    # Buscar os valores novamente após deletar a linha de soma
    valores = aba.get_all_values()  # Recarrega os valores da planilha
    ultima_linha_dados = len(valores)  # Última linha com dados (atualizado após deletar)
    linha_soma = ultima_linha_dados + 2  # Duas linhas abaixo da última linha com dados

    # Colunas para somar, onde A=1, B=2, C=3, etc.
    colunas_somar = {
        'E': 4, 'G': 6, 'H': 7, 'I': 8, 'J': 9,
        'K': 10, 'L': 11, 'M': 12, 'N': 13, 'O': 14, 'T': 19  # Inclui a coluna T (Imposto)
    }

    # Adiciona o texto "Produtos Vendidos: " na coluna D
    aba.update_acell(f"D{linha_soma}", "Produtos Vendidos: ")

    # Para cada coluna especificada, somar os valores e colocar o resultado na linha de soma
    for coluna, indice in colunas_somar.items():
        soma_coluna = 0
        for linha in valores[1:ultima_linha_dados]:  # Ignora o cabeçalho
            valor = linha[indice] if indice < len(linha) else ""  # Evita index out of range
            if valor:  # Se houver um valor
                try:
                    # Remove "R$ " e substitui vírgulas por pontos, depois converte para float
                    valor = valor.replace('R$', '').replace(',', '.').strip()
                    soma_coluna += float(valor)
                except ValueError:
                    print(f"{obter_timestamp()} Valor não numérico encontrado: {valor}. Ignorando.")
                    continue  # Pula valores que não puderem ser convertidos em número

        # Formatação para valores monetários nas colunas G até O e T
        if coluna in ['G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'T']:
            soma_coluna_formatada = f"R$ {soma_coluna:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        else:
            soma_coluna_formatada = soma_coluna  # Para outras colunas, mantém o número simples

        celula_soma = f"{coluna}{linha_soma}"
        aba.update_acell(celula_soma, soma_coluna_formatada)  # Atualiza a célula com o valor da soma formatado

def formatar_valor_moeda(valor):
    """Formata um valor numérico para o formato monetário brasileiro."""
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

#-----------------------------------------------------------------------------------------------------------------------
########################################################################################################################
#----------------------------------------------------- PARTE DA MAGALU -------------------------------------------------



#---------------------------------------------------PARTE DO ESTOQUE DO ML ----------------------------------------------

# ================================================================================================
# FUNÇÃO: obter_ids_anuncios()
# ================================================================================================
# OBJETIVO: Buscar todos os IDs (item_id) dos anúncios/produtos do vendedor no Mercado Livre
# 
# PARÂMETROS:
#   - access_token (str): Token OAuth2 para autenticar as requisições à API do Mercado Livre
#   - user_id (str): ID do vendedor (user_id) cujos anúncios serão buscados
#
# RETORNO: 
#   - Lista com todos os IDs dos anúncios encontrados
#   - Lista vazia [] em caso de erro
#
# FLUXO:
#   1. Monta a URL base para buscar itens do usuário
#   2. Define o cabeçalho Authorization com o access_token
#   3. Faz requisições paginadas (50 itens por vez) até obter todos
#   4. Acumula os IDs em uma lista
#   5. Imprime os IDs encontrados e o total
#   6. Retorna a lista com todos os IDs
#
# EXEMPLO:
#   ids = obter_ids_anuncios(access_token="abc123...", user_id="1100552101")
#   # Retorna: ['999999999', '888888888', '777777777', ...]
# ================================================================================================
def obter_ids_anuncios(access_token, user_id):
    # URL base da API do Mercado Livre para buscar items/anúncios do vendedor
    url = f"https://api.mercadolibre.com/users/{user_id}/items/search"
    
    # Headers da requisição HTTP (autenticação OAuth2)
    headers = {
        'Authorization': f'Bearer {access_token}'
    }

    ids_anuncios = []  # Lista para armazenar todos os IDs encontrados
    offset = 0  # Deslocamento para paginação (começa em 0)
    limit = 50  # Quantidade de resultados por página (máximo 50)

    try:
        # Loop para fazer requisições paginadas até obter todos os resultados
        while True:
            # Monta a URL completa com paginação
            paginated_url = f"{url}?offset={offset}&limit={limit}"
            
            # Faz a requisição GET à API
            response = requests.get(paginated_url, headers=headers)
            response.raise_for_status()  # Levanta exceção se status != 2xx
            
            # Converte a resposta JSON em dicionário
            data = response.json()
            
            # Extrai a lista de resultados (IDs dos anúncios)
            resultados = data.get('results', [])

            # Se não há mais resultados, sai do loop
            if not resultados:
                break

            # Adiciona todos os IDs da página atual à lista
            ids_anuncios.extend(resultados)
            
            # Incrementa o offset para a próxima página
            offset += limit

        # Imprime cada ID em uma linha (útil para copiar e colar)
        for anuncio_id in ids_anuncios:
            print(f'"{anuncio_id}",')

        # Imprime o total de IDs obtidos
        print(f'{obter_timestamp()} Total de IDs obtidos: {len(ids_anuncios)}')

        return ids_anuncios
        
    except requests.exceptions.RequestException as e:
        # Se houver erro de conexão, imprime o erro e retorna lista vazia
        print(f'{obter_timestamp()} Erro ao buscar IDs dos anúncios: {e}')
        return []

# ================================================================================================
# FUNÇÃO: obter_user_product_id()
# ================================================================================================
# OBJETIVO: Buscar o user_product_id e SKU de cada anúncio (item_id) do Mercado Livre
#           Organiza os dados em um dicionário com SKU como chave e lista de user_product_ids
#
# PARÂMETROS:
#   - access_token (str): Token OAuth2 para autenticar requisições à API
#   - item_ids (list): Lista de IDs dos anúncios (item_ids) obtidos por obter_ids_anuncios()
#
# RETORNO:
#   - Dicionário no formato:
#     {
#       'SKU_001': {
#         'anuncios': [{'user_product_id': 'abc123...'}, ...],
#         'estoque_total': 0
#       },
#       'SKU_002': {
#         'anuncios': [{'user_product_id': 'xyz789...'}, ...],
#         'estoque_total': 0
#       },
#       ...
#     }
#   - Dicionário vazio {} em caso de erro
#
# FLUXO:
#   1. Itera por cada item_id fornecido
#   2. Faz requisição à API para obter detalhes do anúncio
#   3. Extrai o user_product_id (necessário para consultar estoque)
#   4. Extrai o SKU a partir dos atributos (SELLER_SKU)
#   5. Agrupa os dados por SKU em um dicionário
#   6. Retorna o dicionário com todos os SKUs e seus user_product_ids
#
# NOTA: Um mesmo SKU pode ter múltiplos user_product_ids se tiver vários anúncios ativo
# ================================================================================================
def obter_user_product_id(access_token, item_ids):
    # Dicionário para armazenar dados organizados por SKU
    sku_estoque = {}
    
    print(f"{obter_timestamp()} Buscando SKUs de todos os anúncios do usuário...")
    
    try:
        # Loop por cada item_id da lista
        for idx, item_id in enumerate(item_ids, 1):  # enumerate começa em 1 para melhor legibilidade
            # URL da API para obter detalhes de um anúncio específico
            url = f"https://api.mercadolibre.com/items/{item_id}"
            
            # Headers com autenticação OAuth2
            headers = {'Authorization': f'Bearer {access_token}'}
            
            # Faz a requisição GET
            response = requests.get(url, headers=headers)
            response.raise_for_status()  # Levanta exceção se houver erro HTTP
            
            # Converte resposta JSON em dicionário
            data = response.json()
            
            # Extrai o user_product_id (usado para consultar estoque)
            user_product_id = data.get('user_product_id')
            
            # Extrai o SKU dos atributos (procura pelo atributo com id='SELLER_SKU')
            # Usa next() para encontrar o primeiro atributo que corresponde, None se não encontrar
            sku = next((attr['value_name'] for attr in data.get('attributes', []) if attr['id'] == 'SELLER_SKU'), None)

            # Imprime progresso (índice atual / total)
            print(f"{obter_timestamp()} [{idx}/{len(item_ids)}] Anúncio {item_id}: SKU={sku}, user_product_id={user_product_id}")

            # Se encontrou tanto SKU quanto user_product_id, adiciona ao dicionário
            if sku and user_product_id:
                # Se é a primeira vez que vê este SKU, cria uma entrada nova
                if sku not in sku_estoque:
                    sku_estoque[sku] = {
                        'anuncios': [{'user_product_id': user_product_id}],
                        'estoque_total': 0  # Será preenchido posteriormente
                    }
                else:
                    # Se SKU já existe, adiciona o novo user_product_id à lista
                    sku_estoque[sku]['anuncios'].append({'user_product_id': user_product_id})

        # Exibe resumo dos dados coletados
        print(f"{obter_timestamp()} Total de SKUs encontrados: {len(sku_estoque)}")
        
        # Calcula o total de user_product_ids (pode ser maior que SKUs se houver duplicatas)
        total_user_product_ids = sum(len(v['anuncios']) for v in sku_estoque.values())
        print(f"{obter_timestamp()} Total de User Product IDs obtidos: {total_user_product_ids}")
        
        # Exibe lista de SKUs encontrados
        print(f"{obter_timestamp()} Lista de SKUs encontrados: {list(sku_estoque.keys())}")
        
        return sku_estoque
        
    except requests.exceptions.RequestException as e:
        # Se houver erro de conexão/requisição, imprime e retorna dicionário vazio
        print(f'{obter_timestamp()} Erro ao buscar detalhes do anúncio: {e}')
        return {}

# ================================================================================================
# FUNÇÃO: obter_estoque_meli_facility()
# ================================================================================================
# OBJETIVO: Consultar o estoque de cada user_product_id na API do Mercado Livre
#           Filtra apenas o tipo de localização 'meli_facility' (Fulfillment Center)
#           Agrega o estoque total por SKU
#
# PARÂMETROS:
#   - access_token (str): Token OAuth2 para autenticar requisições à API
#   - sku_estoque (dict): Dicionário retornado por obter_user_product_id()
#                        Formato: {'SKU': {'anuncios': [{'user_product_id': '...'}, ...], ...}, ...}
#
# RETORNO:
#   - Dicionário no formato:
#     {
#       'SKU_001': 100,  # Total de unidades em meli_facility
#       'SKU_002': 250,
#       ...
#     }
#   - Dicionário vazio {} em caso de erro
#
# FLUXO:
#   1. Itera por cada SKU do dicionário sku_estoque
#   2. Para cada user_product_id do SKU, consulta o estoque na API
#   3. Busca por localizações do tipo 'meli_facility'
#   4. Acumula a quantidade total de cada localização
#   5. Armazena o total acumulado por SKU
#   6. Retorna um dicionário com SKU -> quantidade total
#
# OBS: meli_facility = Armazém do Mercado Livre (Fulfillment Center)
#      Se houver múltiplos user_product_ids para o mesmo SKU, soma todos
# ================================================================================================
def obter_estoque_meli_facility(access_token, sku_estoque):
    # Headers com autenticação OAuth2
    headers = {'Authorization': f'Bearer {access_token}'}
    
    # Dicionário para armazenar o total de estoque meli_facility por SKU
    estoque_total_por_sku = {}

    try:
        # Loop por cada SKU no dicionário sku_estoque
        for sku, info in sku_estoque.items():
            # Variável para acumular o total de estoque meli_facility deste SKU
            total_meli_facility = 0

            # Loop por cada anúncio (user_product_id) associado a este SKU
            for anuncio in info['anuncios']:
                # Extrai o user_product_id do dicionário
                user_product_id = anuncio.get('user_product_id')

                # Se não houver user_product_id, pula para o próximo
                if not user_product_id:
                    print(f"{obter_timestamp()} Anúncio em SKU {sku} não contém 'user_product_id'.")
                    continue

                # URL da API para consultar estoque de um user_product_id
                url = f"https://api.mercadolibre.com/user-products/{user_product_id}/stock"
                
                # Faz requisição GET à API
                response = requests.get(url, headers=headers)
                response.raise_for_status()  # Levanta exceção se houver erro
                
                # Converte resposta JSON
                data = response.json()
                
                # Extrai a lista de localizações (locations)
                locations = data.get('locations', [])

                # Filtra apenas localizações do tipo 'meli_facility' (Fulfillment Center)
                for location in locations:
                    if location.get('type') == 'meli_facility':
                        # Obtém a quantidade de estoque nesta localização
                        meli_facility_quantity = location.get('quantity', 0)
                        
                        # Acumula à quantidade total deste SKU
                        total_meli_facility += meli_facility_quantity

                # LINHA COMENTADA (útil para debug):
                # print(f'SKU: {sku}, User Product ID {user_product_id}: Meli Facility Quantity = {meli_facility_quantity}')

            # Armazena o total acumulado de meli_facility para este SKU
            estoque_total_por_sku[sku] = total_meli_facility
            print(f'{obter_timestamp()} Total Meli Facility para SKU {sku}: {total_meli_facility}')

        # Retorna o dicionário com estoque total por SKU
        return estoque_total_por_sku
        
    except requests.exceptions.RequestException as e:
        # Se houver erro de conexão, imprime e retorna dicionário vazio
        print(f'{obter_timestamp()} Erro ao buscar estoque do produto: {e}')
        return {}

# ================================================================================================
# FUNÇÃO: sincronizar_estoque_full_ml()
# ================================================================================================
# OBJETIVO: Sincronizar o estoque do Fulfillment Center (FULL ML) com a aba principal de estoque
#           Integra dados de múltiplas fontes: Bling + FULL ML + Magalu
#           Atualiza SKUs existentes e adiciona SKUs novos ao final da planilha
#
# PARÂMETROS:
#   - estoque_total_por_sku (dict): Dicionário {'SKU': quantidade, ...} de estoque FULL ML
#   - aba_estoque (worksheet): Objeto gspread da aba "estoque" onde será feita a sincronização
#
# ESTRUTURA DE COLUNAS DA ABA "estoque":
#   A: SKU (código do produto)
#   B: Bling (estoque do sistema Bling)
#   C: FULL ML (estoque em Fulfillment Center do Mercado Livre)
#   D: Magalu (estoque no marketplace Magalu)
#   E: Total (soma: B + C + D)
#
# FLUXO:
#   1. Lê todos os valores da aba estoque
#   2. Cria um dicionário com SKUs existentes (para rápida busca)
#   3. Para cada SKU em estoque_total_por_sku:
#      a. Se SKU existe: atualiza colunas C e E (FULL ML e Total)
#      b. Se SKU é novo: adiciona uma linha nova ao final
#   4. Calcula o Total como: Bling + FULL ML + Magalu
#   5. Trata valores vazios/inválidos com segurança
#
# NOTAS IMPORTANTES:
#   - Células vazias não geram erro, são tratadas como 0
#   - Valores negativos são ignorados (considerados como 0)
#   - O Total nunca fica vazio (mínimo 0)
#   - SKUs novos são adicionados ao final da planilha via insert_rows
# ================================================================================================
def sincronizar_estoque_full_ml(estoque_total_por_sku, aba_estoque):
    try:
        print(f"{obter_timestamp()} Iniciando sincronização do estoque FULL ML com a aba principal...")
        
        # Lê todos os valores da aba estoque em um único batch (mais rápido)
        dados_aba_estoque = aba_estoque.get_all_values()
        
        # Se a aba está vazia, não faz nada
        if not dados_aba_estoque:
            print(f"{obter_timestamp()} Aba 'estoque' está vazia.")
            return
            
        # Cria um dicionário com SKUs existentes para acesso rápido por SKU
        # Estrutura: {'SKU': {'linha': 2, 'bling': '10', 'full_ml': '5', 'magalu': '0', 'total': '15'}, ...}
        skus_bling = {}
        for i, linha in enumerate(dados_aba_estoque[1:], start=2):  # Começa da linha 2 (ignora cabeçalho)
            if linha and linha[0]:  # Se há conteúdo na coluna A (SKU)
                sku = linha[0].strip()  # Remove espaços em branco
                
                # Armazena dados de cada coluna (tratando índices que podem não existir)
                skus_bling[sku] = {
                    'linha': i,
                    'bling': linha[1] if len(linha) > 1 else '',
                    'full_ml': linha[2] if len(linha) > 2 else '',
                    'magalu': linha[3] if len(linha) > 3 else '',
                    'total': linha[4] if len(linha) > 4 else ''
                }
        
        # Lista para armazenar atualizações de SKUs existentes
        atualizacoes = []
        
        # Lista para armazenar SKUs novos a serem adicionados
        skus_novos = []
        
        # Processa cada SKU do estoque FULL ML obtido da API
        for sku, quantidade_full in estoque_total_por_sku.items():
            
            # Verifica se o SKU já existe na aba estoque
            if sku in skus_bling:
                # ===== SKU EXISTE - ATUALIZAR =====
                
                # Número da linha onde o SKU está
                linha_num = skus_bling[sku]['linha']
                
                # Obtém valores de Bling e Magalu para recalcular o Total
                bling = skus_bling[sku]['bling']
                magalu = skus_bling[sku]['magalu']
                
                # Inicia o cálculo do total em 0
                total = 0
                
                # ===== CALCULA BLING =====
                # Só inclui se houver valor e for positivo
                if bling and str(bling).strip():
                    try:
                        # Converte valor (substitui vírgula por ponto)
                        valor_bling = float(str(bling).replace(',', '.'))
                        # Só soma se for positivo
                        if valor_bling > 0:
                            total += valor_bling
                    except ValueError:
                        # Se não conseguir converter, ignora (não quebra o código)
                        pass
                
                # ===== CALCULA FULL ML =====
                # Valor vem da API, já é um número
                if quantidade_full > 0:
                    total += quantidade_full
                
                # ===== CALCULA MAGALU =====
                # Só inclui se houver valor e for positivo
                if magalu and str(magalu).strip():
                    try:
                        # Converte valor (substitui vírgula por ponto)
                        valor_magalu = float(str(magalu).replace(',', '.'))
                        # Só soma se for positivo
                        if valor_magalu > 0:
                            total += valor_magalu
                    except ValueError:
                        # Se não conseguir converter, ignora
                        pass
                
                # Garante que o total nunca fica vazio (mínimo 0)
                total = max(total, 0)
                
                # Adiciona à lista de atualizações (será aplicada em batch)
                # Atualiza: Coluna C (FULL ML), Coluna D (Magalu), Coluna E (Total)
                atualizacoes.append({
                    'range': f'C{linha_num}:E{linha_num}',
                    'values': [[quantidade_full, magalu, total]]
                })
                
                # Imprime log da atualização
                print(f"{obter_timestamp()} Atualizando SKU {sku}: FULL ML={quantidade_full}, Total={total}")
                
            else:
                # ===== SKU NÃO EXISTE - ADICIONAR =====
                
                # Calcula o total apenas com FULL ML (Bling e Magalu vazios)
                total_novo = max(quantidade_full, 0)
                
                # Adiciona à lista de SKUs novos: [SKU, Bling, FULL ML, Magalu, Total]
                skus_novos.append([sku, '', quantidade_full, '', total_novo])
                
                # Imprime log da adição
                print(f"{obter_timestamp()} Novo SKU {sku}: FULL ML={quantidade_full}, Total={total_novo}")
        
        # ===== APLICA TODAS AS ATUALIZAÇÕES DE UMA VEZ (batch update) =====
        if atualizacoes:
            for atualizacao in atualizacoes:
                aba_estoque.update(atualizacao['range'], atualizacao['values'])
        
        # ===== ADICIONA SKUS NOVOS AO FINAL DA PLANILHA =====
        if skus_novos:
            # Calcula a linha onde será feita a inserção (última linha + 1)
            ultima_linha = len(dados_aba_estoque) + 1
            # Insere as novas linhas
            aba_estoque.insert_rows(skus_novos, ultima_linha)
            print(f"{obter_timestamp()} Adicionados {len(skus_novos)} novos SKUs ao final da planilha.")
        
        # Log de conclusão
        print(f"{obter_timestamp()} Sincronização do estoque FULL ML concluída com sucesso!")
        
    except Exception as e:
        # Se houver qualquer erro, imprime mensagem detalhada
        print(f"{obter_timestamp()} Erro ao sincronizar estoque: {e}")

# ================================================================================================
# FUNÇÃO: atualizar_planilha_estoque()
# ================================================================================================
# OBJETIVO: Atualizar a aba "ESTOQUE ML" com dados de estoque do Fulfillment Center
#           Esta aba serve para manter histórico/log de estoque por data
#
# PARÂMETROS:
#   - estoque_total_por_sku (dict): Dicionário {'SKU': quantidade, ...} de estoque FULL ML
#   - aba_estoque_ml (worksheet): Objeto gspread da aba "ESTOQUE ML"
#
# ESTRUTURA DA ABA "ESTOQUE ML":
#   A: SKU (código do produto)
#   B: Quantidade (estoque em meli_facility)
#   (Opcionalmente pode ter data em C, D, etc. para histórico)
#
# FLUXO:
#   1. Prepara dados para inserção (cada SKU em coluna A, quantidade em coluna B)
#   2. Calcula número de linhas existentes
#   3. Se houver linhas além do cabeçalho, limpa a área A2:B{última linha}
#   4. Insere todos os novos dados a partir da linha 2
#   5. Mantém o cabeçalho intacto (linha 1)
#
# NOTA: Esta função substitui completamente o conteúdo anterior
#       (não adiciona/incrementa, mas reescreve tudo de zero)
# ================================================================================================
def atualizar_planilha_estoque(estoque_total_por_sku, aba_estoque_ml):
    try:
        # Prepara os dados para inserir: cada SKU na coluna A, quantidade na coluna B
        # Formato: [[SKU_001, 100], [SKU_002, 250], ...]
        dados_a_inserir = []
        for sku, quantidade in estoque_total_por_sku.items():
            dados_a_inserir.append([sku, quantidade])

        # Obtém todos os valores da planilha (incluindo cabeçalho)
        todas_as_linhas = aba_estoque_ml.get_all_values()
        
        # Calcula o número total de linhas
        num_linhas = len(todas_as_linhas)
        
        # Se há mais de 1 linha (cabeçalho + dados), limpa os dados existentes
        # Mantém apenas a linha 1 (cabeçalho)
        if num_linhas > 1:
            # Limpa do A2 até B na última linha (remove todos os dados)
            aba_estoque_ml.batch_clear([f"A2:B{num_linhas}"])

        # Insere os novos dados a partir da linha 2 (logo após o cabeçalho)
        if dados_a_inserir:
            aba_estoque_ml.insert_rows(dados_a_inserir, 2)

        # Log de conclusão
        print(f"{obter_timestamp()} Estoque atualizado na planilha com todos os SKUs e quantidades.")
        
    except Exception as e:
        # Se houver erro, imprime mensagem detalhada
        print(f"{obter_timestamp()} Erro ao atualizar a planilha: {e}")

#-------------------------------------------------------- MAIN ---------------------------------------------------------

# Atualizando a função principal para incluir a soma após a organização dos dados
def main():
    # Carregar credenciais das variáveis de ambiente
    client_id = os.getenv('ML_CLIENT_ID')
    client_secret = os.getenv('ML_CLIENT_SECRET')
    refresh_token = os.getenv('ML_REFRESH_TOKEN')
    seller_id = os.getenv('SELLER_ID', '1100552101')
    
    # Validar se as credenciais foram carregadas
    if not client_id or not client_secret or not refresh_token:
        print(f"{obter_timestamp()} ❌ ERRO: Credenciais Mercado Livre não encontradas!")
        print(f"{obter_timestamp()} Verifique se as variáveis de ambiente ML_CLIENT_ID, ML_CLIENT_SECRET e ML_REFRESH_TOKEN estão definidas.")
        return

    # Caminhos do arquivo JSON (suporta múltiplas localizações)
    possible_paths = [
        os.path.expanduser('~/Área de trabalho/Arquivo_Json_AutomacaoPlanilhaBruno.json'),
        'Arquivo_Json_AutomacaoPlanilhaBruno.json',
        '/tmp/Arquivo_Json_AutomacaoPlanilhaBruno.json',
    ]
    
    caminho_arquivo_json = None
    for path in possible_paths:
        if os.path.exists(path):
            caminho_arquivo_json = path
            print(f"{obter_timestamp()} ✅ Arquivo JSON encontrado em: {path}")
            break
    
    if not caminho_arquivo_json:
        print(f"{obter_timestamp()} ❌ ERRO: Arquivo JSON do Google Sheets não encontrado!")
        print(f"{obter_timestamp()} Procurado em: {possible_paths}")
        return

    escopos = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    credenciais = ServiceAccountCredentials.from_json_keyfile_name(caminho_arquivo_json, scopes=escopos)
    cliente_sheets = gspread.authorize(credenciais)

    planilha = cliente_sheets.open("Relatórios de Venda Automatizado - Mercado Livre")
    aba = planilha.worksheet("ML MAIO 26")
    aba_dados = planilha.worksheet("Dados")
    # aba_metas = planilha.worksheet("METAS")  # Comentado: aba não existe ainda
    aba_estoque_ml = planilha.worksheet("ESTOQUE ML")
    aba_estoque_principal = planilha.worksheet("estoque")  # Aba principal de estoque (Bling)

    while True:  # Loop para verificar novos pedidos a cada 5 minutos
        try:
            print(f"{obter_timestamp()} ========== INICIANDO CICLO DE VERIFICAÇÃO ==========")
            
            access_token = atualizar_access_token(client_id, client_secret, refresh_token)
            if not access_token:
                print(f"{obter_timestamp()} Falha ao obter access token, encerrando...")
                return 

            date_from = '2024-12-01T00:00:00.000Z'
            date_to = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')

            print(f"{obter_timestamp()} Obtendo pedidos existentes na planilha...")
            # Obter IDs de pedidos já presentes na planilha
            ids_existentes = obter_ids_existentes(aba)

            print(f"{obter_timestamp()} Requisitando pedidos do Mercado Livre...")
            # Requisitar os pedidos
            pedidos = requisitar_pedidos(access_token, seller_id, date_from, date_to)

            print(f"{obter_timestamp()} Filtrando pedidos PAID de maio 2026...")
            # Filtrar pedidos com status 'PAID' e de maio de 2026
            pedidos_filtrados = filtrar_pedidos_paid_fevereiro(pedidos)

            print(f"{obter_timestamp()} Inserindo novos pedidos na planilha...")
            # Inserir novos pedidos na planilha
            inserir_dados_planilha(pedidos_filtrados, aba, ids_existentes, aba_dados, access_token)

            print(f"{obter_timestamp()} Organizando planilha...")
            # Organizar a planilha
            organizar_planilha(aba)

            print(f"{obter_timestamp()} Adicionando somas na planilha...")
            # Adicionar as somas ao final da planilha
            adicionar_somas_planilha(aba)



            # Atualizar a aba de Metas
            #atualizar_metas(aba, aba_metas)

            # Gerar e enviar o relatório das metas
            #gerar_relatorio_metas(aba_metas)
            #print("Relatório enviado!")

            #print(f"{obter_timestamp()} Iniciando processo de estoque...")
            # Obter IDs dos anúncios do vendedor
            #ids_anuncios = obter_ids_anuncios(access_token, seller_id)

            #if ids_anuncios:
                #print(f"{obter_timestamp()} Obtendo User Product IDs...")
                # Obter User Product IDs dos anúncios obtidos
                #user_product_ids = obter_user_product_id(access_token, ids_anuncios)

            #print(f"{obter_timestamp()} Obtendo estoque FULL ML...")
            # Obter o estoque total por SKU
            #estoque_total_por_sku = obter_estoque_meli_facility(access_token, user_product_ids)
            
            #print(f"{obter_timestamp()} Atualizando aba ESTOQUE ML...")
            # Atualizar a aba "ESTOQUE ML" (para manter histórico)
            #atualizar_planilha_estoque(estoque_total_por_sku, aba_estoque_ml)
            
            #print(f"{obter_timestamp()} Sincronizando com aba principal de estoque...")
            # Sincronizar com a aba "estoque" principal (Bling + FULL ML + Magalu)
            #sincronizar_estoque_full_ml(estoque_total_por_sku, aba_estoque_principal)

            #obter_estoque_meli_facility(access_token, user_product_ids)
            print(f"{obter_timestamp()} Access Token: {access_token}")
            print(f"{obter_timestamp()} ========== CICLO CONCLUÍDO COM SUCESSO ==========")
            print(f"{obter_timestamp()} Aguardando 10 minutos para a próxima execução...")
            time.sleep(600)  # Aguarda 10 minutos antes de repetir o processo

        except Exception as e:
            print(f"{obter_timestamp()} ❌ ERRO CRÍTICO: {e}")
            print(f"{obter_timestamp()} Aguardando 1 minuto antes de tentar novamente...")
            time.sleep(60)  # Se ocorrer um erro, aguarda 1 minuto antes de tentar novamente

if __name__ == "__main__":
    main()






    #email inquilino: carlos.pereira@email.com
    #Senha inquilino: CARL4455
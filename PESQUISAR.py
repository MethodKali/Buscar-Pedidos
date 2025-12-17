import streamlit as st
import pandas as pd
import datetime
import re
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import date, timedelta
import calendar 
import json
import os # Importar 'os' para checagem de ambiente

# --- CONFIGURAÇÃO DE ACESSO E LIMITES ---
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
# Define o nome do arquivo para desenvolvimento local
CREDS_FILE = "acesso.json" 
SPREADSHEET_ID = "1X9trwwqVCwPXY2_O667WJcOR4CHNYbBjJDVsrYNZSgc"     

# ----------------------------------------------------------------------
# MAPA DE COLUNAS (Mantido)
# ----------------------------------------------------------------------
COL_PEDIDO = "PEDIDO"
COL_STATUS = "STATUS"
COL_DATA = "DATA"
COL_VALOR = "VALOR"
COL_UNIDADE = "UNIDADE"
COL_CARRO = "CARRO | UTILIZAÇÃO"
COL_FORNECEDOR = "FORNECEDOR"

# -----------------------
# FUNÇÕES DE VALOR E FORMATAÇÃO (Mantidas)
# -----------------------

def valor_brasileiro(valor):
    if pd.isna(valor) or valor is None:
        return 0.0
    s = str(valor).strip()
    s = re.sub(r"[R$\s\.]", "", s)
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0

def br_money(valor):
    if pd.isna(valor):
        return "R$ 0,00"
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def safe_load(df):
    df = df.copy()
    date_cols_to_process = [c for c in [COL_DATA] if c in df.columns]

    for col in date_cols_to_process:
        df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce").dt.normalize()

    if COL_VALOR in df.columns:
        df[COL_VALOR] = df[COL_VALOR].apply(valor_brasileiro)
    
    if COL_DATA in df.columns:
        df = df[pd.notna(df[COL_DATA])].copy()

    return df

# -----------------------
# FUNÇÃO DE CÁLCULO DO NOME DA ABA DE BACKUP (ATUALIZADA)
# -----------------------
def calculate_backup_sheet_name() -> str:
    # Obtém a data atual baseada no fuso horário de SP para evitar erros no servidor
    SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')
    today = datetime.datetime.now(SAO_PAULO_TZ).date()
    
    # Segunda-feira é 0, Sexta-feira é 4
    is_monday = today.weekday() == calendar.MONDAY

    if is_monday:
        # Se hoje é segunda, o backup é da semana que terminou na sexta passada
        # Sexta passada foi há 3 dias
        ultimo_dia_util = today - timedelta(days=3)
    else:
        # Se não é segunda, precisamos achar a sexta-feira da SEMANA PASSADA
        # Calculamos quantos dias se passaram desde a última sexta
        days_since_friday = (today.weekday() - calendar.FRIDAY) % 7
        
        if today.weekday() in [calendar.SATURDAY, calendar.SUNDAY]:
            # No fim de semana, a "última sexta" ainda é a da semana atual
            ultimo_dia_util = today - timedelta(days=days_since_friday)
        else:
            # Durante a semana (Ter-Sex), a "última sexta" relevante é a da semana anterior
            ultimo_dia_util = today - timedelta(days=days_since_friday + 7)

    # A aba sempre compreende o intervalo de Segunda (4 dias antes da Sexta) a Sexta
    primeiro_dia_util = ultimo_dia_util - timedelta(days=4)

    # Retorna no formato exato das abas: "DD.MM a DD.MM"
    return f"{primeiro_dia_util.strftime('%d.%m')} a {ultimo_dia_util.strftime('%d.%m')}"

@st.cache_data(ttl=300)
def load_sheets(today_str):
    gc = None
    try:

        
        # 1. Tenta carregar credenciais das Secrets (para Streamlit Cloud)
        creds_json = st.secrets.get("google_sheets_service_account")
        
        if creds_json:
            # Autenticação usando Secrets (dicionário JSON)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(creds_json), SCOPE)
            gc = gspread.authorize(creds)
            # st.success("Autenticado via Streamlit Secrets (Deploy).") # Opcional para debug
        
        elif os.path.exists(CREDS_FILE):
            # 2. Tenta carregar credenciais do arquivo local (para desenvolvimento)
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
            gc = gspread.authorize(creds)
            # st.info("Autenticado via arquivo local (acesso.json).") # Opcional para debug
        
        else:
            raise FileNotFoundError("Credenciais (Secrets ou acesso.json) não encontradas.")
            
    except Exception as e:
        st.error(f"Erro ao autenticar credenciais. Verifique as Secrets. Erro: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    
    try:
        sh = gc.open_by_key(SPREADSHEET_ID)
    except Exception as e:
        st.error(f"Erro ao abrir a planilha. Verifique o ID e as credenciais. Erro: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


    def load_sheet_as_df(sheet_name):
        try:
            data = sh.worksheet(sheet_name).get_all_values() 
            # ... (lógica de cabeçalhos única e load safe_load mantida)
            raw_headers = [h.strip().upper() for h in data[1]]
            seen_headers = {}
            unique_headers = []
            
            for header in raw_headers:
                clean_header = header if header else ""
                if clean_header in seen_headers:
                    seen_headers[clean_header] += 1
                    unique_headers.append(f"{clean_header}_{seen_headers[clean_header]}") 
                else:
                    seen_headers[clean_header] = 0
                    unique_headers.append(clean_header)
            
            df = pd.DataFrame(data[2:], columns=unique_headers)
            df.replace('', pd.NA, inplace=True)
            df.dropna(how='all', inplace=True) 
            
            return safe_load(df) 
        
        except gspread.WorksheetNotFound:
            return pd.DataFrame()
        except Exception as e:
            st.error(f"Erro ao carregar aba {sheet_name}. Erro: {e}")
            return pd.DataFrame()

    df_alta = load_sheet_as_df("ALTA")
    df_emerg = load_sheet_as_df("EMERGENCIAL")
    
    BACKUP_SHEET_NAME = calculate_backup_sheet_name()
    df_backup = load_sheet_as_df(BACKUP_SHEET_NAME)

    return df_alta, df_emerg, df_backup


# -----------------------
# APP STREAMLIT - INTERFACE (Mantida)
# -----------------------

# --- CONFIGURAÇÃO DA SIDEBAR MÍNIMA ---
st.sidebar.image("saritur1.png")

SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')
today_date_tz = datetime.datetime.now(SAO_PAULO_TZ).date()
today_date_str = today_date_tz.isoformat() 

if st.sidebar.button("🔄 Recarregar Dados"):
    st.cache_data.clear()
    st.success("Cache limpo! Recarregando dados...")
    
df_alta, df_emerg, df_backup = load_sheets(today_date_str)

# --- RODAPÉ DA SIDEBAR ---
st.sidebar.markdown("---") 
st.sidebar.markdown(
    """
    <p style='font-size: 11px; color: #808489; text-align: center;'>
    Desenvolvido por Kerles Alves - Ass. Suprimentos
    </p>
    """,
    unsafe_allow_html=True
)
st.sidebar.markdown(
    """
    <p style='font-size: 11px; color: #808489; text-align: center;'>
    Unidade Jardim Montanhês (BH) - Saritur Santa Rita Transporte Urbano e Rodoviário
    </p>
    """,
    unsafe_allow_html=True
)

# -----------------------
# CORPO PRINCIPAL DO APP - PESQUISA DE PEDIDOS (Mantida)
# -----------------------

st.title("Sistema de Pesquisa de Pedidos – ALTA, EMERGENCIAL e BACKUP")

# Exibe o nome da aba de backup que está sendo rastreada
try:
    BACKUP_SHEET_NAME = calculate_backup_sheet_name()
    st.info(f"Aba de Backup de Emergencial sendo rastreada: **{BACKUP_SHEET_NAME}**")
except Exception:
    pass 


## 1) Pesquisa por Número de Pedido

st.subheader("🔍 Situação da Solicitação/Pedido")
pedido_input = st.text_input("Digite o número do pedido:", help="Ex: 5678/2025")

def show_result(row, sheet_name):
    """Função para exibir os resultados encontrados."""
    st.write(f"📁 **Origem:** {sheet_name}") 
    st.write(f"📅 **Previsão de pagamento:** {row.get(COL_DATA).strftime('%d/%m/%Y')}") 
    st.write(f"📌 **Status:** {row.get(COL_STATUS)}")
    st.write(f"💰 **Valor:** {br_money(row.get(COL_VALOR))}")
    st.write(f"🏢 **Unidade solicitante:** {row.get(COL_UNIDADE)}")
    st.write(f"🚌 **Carro/Utilização:** {row.get(COL_CARRO)}")
    st.write(f"📦 **Fornecedor:** {row.get(COL_FORNECEDOR)}")
    st.write("---")


if pedido_input:
    pid = pedido_input.strip().upper() 
    
    def search_df(df, pid):
        """Busca o pedido no DataFrame."""
        if COL_PEDIDO in df.columns and not df.empty:
            return df[df[COL_PEDIDO].astype(str).str.strip().str.upper() == pid]
        return pd.DataFrame()

    res_alta = search_df(df_alta, pid)
    res_emerg = search_df(df_emerg, pid)
    res_backup = search_df(df_backup, pid) 

    if res_alta.empty and res_emerg.empty and res_backup.empty:
        st.warning(f"❌ Pedido '{pedido_input}' não encontrado em nenhuma aba.")
    else:
        
        if not res_alta.empty:
            st.success("🟦 Pedido encontrado na aba ALTA")
            show_result(res_alta.iloc[0], "ALTA")

        if not res_emerg.empty:
            st.success("🟥 Pedido encontrado na aba EMERGENCIAL")
            show_result(res_emerg.iloc[0], "EMERGENCIAL")
            
        if not res_backup.empty:
            st.info(f"🗄️ Pedido encontrado na aba de BACKUP: {BACKUP_SHEET_NAME}")
            show_result(res_backup.iloc[0], BACKUP_SHEET_NAME)
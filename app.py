import streamlit as st
import fitz  # PyMuPDF
import re
from datetime import datetime
from PyPDFForm.wrapper import PdfWrapper

# --- CONFIGURAÇÃO DA PÁGINA E CONSTANTES ---
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="wide")
st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.markdown("Envie a **Ficha AIH (PDF)** para pré-preencher o formulário e gerar a ficha HEMOBA final.")
HEMOBA_TEMPLATE_PATH = 'modelo_hemo.pdf'

# ==============================================================================
# 1. FUNÇÕES AUXILIARES DE LIMPEZA
# ==============================================================================
def limpar_nome(nome):
    """Extrai apenas sequências de letras e espaços, removendo números e símbolos."""
    if not nome:
        return ""
    palavras = re.findall(r"[A-Za-zÀ-ÿ\\s]+", nome)
    return ' '.join(palavras).strip()

def limpar_numeros(texto):
    """Extrai apenas os dígitos de uma string."""
    if not texto:
        return ""
    return re.sub(r"\\D", "", texto)

# ==============================================================================
# 2. FUNÇÕES DE EXTRAÇÃO DE DADOS (LÓGICA HÍBRIDA)
# ==============================================================================
def extract_data_pass_A(full_text):
    """Passagem A: Extração rápida usando Regex no texto completo."""
    results = {}
    patterns = {
        'nome_paciente': r"Nome do Paciente\\s*[\\:\\-\\s\\n]+(.+)",
        # CNS pode ter 11 a 15 dígitos dependendo da fonte
        'cartao_sus': r"CNS\\s*[\\:\\-\\s\\n]+(\\d{11,15})",
        'data_nascimento': r"Data de Nasc\\s*[\\:\\-\\s\\n]+(\\d{2}/\\d{2}/\\d{4})",
        'sexo': r"Sexo\\s*[\\:\\-\\s\\n]+(Masculino|Feminino)",
        'nome_genitora': r"Nome da Mãe\\s*[\\:\\-\\s\\n]+(.+)",
        'endereco_residencia': r"Endereço Residencial \\(Rua, Av etc\\)\\s*[\\:\\-\\s\\n]+(.+)",
        'municipio_origem': r"Município de Referência\\s*[\\:\\-\\s\\n]+([A-ZÀ-ÿ\\s\\-]+)",
        'prontuario': r"Núm\\. Prontuário\\s*[\\:\\-\\s\\n]+(\\d+)",
        'hospital': r"Nome do Estabelecimento Solicitante\\s*[\\:\\-\\s\\n]+(.+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            results[key] = match.group(1).strip()
    return results

def extract_data_pass_B(page):
    """Passagem B: Extração de resgate usando coordenadas dos blocos de texto."""
    results = {}
    blocks = page.get_text("blocks")
    blocks.sort(key=lambda b: (b[1], b[0]))
    # Coordenadas aproximadas com base no layout da AIH
    coordinate_map = {
        'nome_genitora': (510, 540),
        'nome_paciente': (590, 620),
        'cartao_sus': (675, 705),
        'prontuario': (730, 760),
    }
    for key, (y0, y1) in coordinate_map.items():
        for block in blocks:
            y_center = (block[1] + block[3]) / 2
            if y0 <= y_center <= y1:
                text = block[4].strip()
                if key == 'prontuario':
                    match = re.search(r"(\\d+)", text)
                    if match:
                        results[key] = match.group(1)
                elif key == 'cartao_sus':
                    results[key] = limpar_numeros(text)
                else:
                    results[key] = limpar_nome(text)
                break
    return results

def extract_data_from_aih(pdf_stream):
    """
    Orquestrador: executa a extração em múltiplas passagens.

    Além de extrair os campos, grava o texto bruto em `extracted_text.txt` e
    armazena `raw_text` no estado da sessão para depuração.
    """
    try:
        doc = fitz.open(stream=pdf_stream.read(), filetype="pdf")
        if not doc:
            return {}
        page = doc[0]
        full_text = page.get_text("text") or ""

        # Grava log do texto extraído
        try:
            with open("extracted_text.txt", "w", encoding="utf-8") as f:
                f.write(full_text)
        except Exception as err:
            st.warning(f"Falha ao salvar extracted_text.txt: {err}")

        # Armazena no estado da sessão para exibir opcionalmente no app
        st.session_state["raw_text"] = full_text

        # Passagem A
        results = extract_data_pass_A(full_text)

        # Verifica campos críticos e, se necessário, Passagem B
        critical_fields = ["nome_paciente", "nome_genitora", "cartao_sus", "prontuario"]
        missing = [f for f in critical_fields if not results.get(f)]
        if missing:
            st.warning(
                f"Passagem A não encontrou: {', '.join(missing)}. Tentando resgate por posição..."
            )
            rescue = extract_data_pass_B(page)
            for field in missing:
                if rescue.get(field) and not results.get(field):
                    results[field] = rescue[field]
                    st.info(f"✔️ Campo '{field}' resgatado com sucesso!")

        # Limpeza final e preenchimento automático de data/hora
        for key in ["nome_paciente", "nome_genitora"]:
            if key in results and results[key]:
                results[key] = limpar_nome(results[key])
        results["data"] = datetime.now().strftime("%Y-%m-%d")
        results["hora"] = datetime.now().strftime("%H:%M")

        st.success("Extração finalizada.")
        return results
    except Exception as e:
        st.error(f"Erro ao processar PDF: {e}")
        return {}

# ==============================================================================
# 3. FUNÇÃO DE PREENCHIMENTO DE PDF
# ==============================================================================
def fill_hemoba_pdf(template_path, data):
    """
    Preenche o PDF modelo da HEMOBA com os dados coletados.
    Checkboxes são tratados como booleanos, enquanto os demais campos viram strings.
    """
    try:
        data_for_pdf = {k: str(v if v is not None else "") for k, v in data.items()}
        for field in ["antecedente_transfusional", "antecedentes_obstetricos", "reacao_transfusional"]:
            selection = data.get(field)
            data_for_pdf[f"{field}s"] = selection == "Sim"
            data_for_pdf[f"{field}n"] = selection == "Não"
        modalidades = {
            "Programada": "modalidade_transfusaop",
            "Rotina": "modalidade_transfusaor",
            "Urgência": "modalidade_transfusaou",
            "Emergência": "modalidade_transfusaoe",
        }
        selected = data.get("modalidade_transfusao")
        for key, pdf_field in modalidades.items():
            data_for_pdf[pdf_field] = key == selected
        for product in ["hema", "pfc", "plaquetas_prod", "crio"]:
            data_for_pdf[product] = data.get(product) is True

        pdf_form = PdfWrapper(template_path)
        pdf_form.fill(data_for_pdf, flatten=False)
        return pdf_form.read()
    except Exception as e:
        st.error(f"Erro ao preencher o PDF: {e}")
        raise

# ==============================================================================
# 4. INTERFACE DO APLICATIVO (STREAMLIT)
# ==============================================================================
# Inicializa o estado da sessão para guardar os dados
if "form_data" not in st.session_state:
    st.session_state.form_data = {}

# --- Seção de Upload ---
with st.container(border=True):
    st.header("1. Enviar Ficha AIH")
    uploaded_file = st.file_uploader(
        "Selecione o arquivo PDF da AIH",
        type="pdf",
        label_visibility="collapsed"
    )

    # Ao enviar um arquivo, extraímos imediatamente (não precisa de botão)
    if uploaded_file is not None:
        if st.session_state.get("last_uploaded_name") != uploaded_file.name:
            with st.spinner("Lendo a AIH..."):
                st.session_state.form_data = extract_data_from_aih(uploaded_file)
                st.session_state["last_uploaded_name"] = uploaded_file.name
                if st.session_state.form_data:
                    st.success("Dados extraídos! Revise e complete abaixo.")
                else:
                    st.error("Não foi possível extrair dados. Verifique o PDF.")

# --- Seção do Formulário de Revisão ---
if st.session_state.form_data:
    data = st.session_state.form_data
    with st.form("hemoba_form"):
        st.header("2. Revisar e Completar Formulário")

        st.subheader("Dados Extraídos (Automático)")
        col1, col2 = st.columns(2)
        with col1:
            st.text_input("Nome do Paciente", value=data.get("nome_paciente", ""), key="nome_paciente")
            st.text_input("Nome da Mãe", value=data.get("nome_genitora", ""), key="nome_genitora")
        with col2:
            st.text_input("Cartão SUS", value=data.get("cartao_sus", ""), key="cartao_sus")
            st.text_input("Prontuário", value=data.get("prontuario", ""), key="prontuario")

        # Exibe o texto extraído dentro de um expander para debug
        with st.expander("Ver texto extraído (debug)"):
            raw = st.session_state.get("raw_text", "")
            st.text_area("Texto completo extraído do PDF", value=raw, height=200)

        st.subheader("Dados para Preenchimento (Manual)")
        col3, col4 = st.columns(2)
        with col3:
            st.text_input("Diagnóstico", key="diagnostico")
            st.selectbox("Antecedente Transfusional?", ["Não", "Sim"], key="antecedente_transfusional")
        with col4:
            st.text_input("Peso (kg)", key="peso")
            st.selectbox("Antecedentes Obstétricos?", ["Não", "Sim"], key="antecedentes_obstetricos")

        st.subheader("Programação (Manual)")
        st.selectbox("Modalidade de Transfusão", ["Rotina", "Programada", "Urgência", "Emergência"], key="modalidade_transfusao")

        if st.form_submit_button("Gerar PDF Final", type="primary"):
            with st.spinner("Gerando o PDF..."):
                # Coleta dados atuais da sessão e adiciona data/hora
                final_data = {k: v for k, v in st.session_state.items()}
                final_data["data"] = datetime.now().strftime("%d/%m/%Y")
                final_data["hora"] = datetime.now().strftime("%H:%M")
                try:
                    pdf_bytes = fill_hemoba_pdf(HEMOBA_TEMPLATE_PATH, final_data)
                    st.success("PDF gerado com sucesso!")
                    st.download_button(
                        label="✔️ Baixar Ficha HEMOBA",
                        data=pdf_bytes,
                        file_name=f"HEMOBA_{final_data.get('nome_paciente', 'paciente').replace(' ', '_')}.pdf",
                        mime="application/pdf"
                    )
                except Exception:
                    # Erros já são capturados dentro de fill_hemoba_pdf
                    pass

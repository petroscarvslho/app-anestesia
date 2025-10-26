import io 
import re
import streamlit as st
import fitz
import traceback
from rapidocr_onnxruntime import RapidOCR
from PIL import Image, ImageOps, ExifTags
import numpy as np
import cv2

# --- CONFIGURAÇÃO E FUNÇÕES AUXILIARES ---
st.set_page_config(page_title="Analisador de Laudo AIH", layout="centered")
st.markdown("""<style>.block-container {max-width: 740px !important; padding-top: 1.2rem;}</style>""", unsafe_allow_html=True)

def limpar_texto(txt: str) -> str:
    return re.sub(r"\s+", " ", txt).strip() if txt else ""

def so_digitos(txt: str) -> str:
    return re.sub(r"\D", "", txt or "")

# === ADIÇÃO 3: PÓS-PROCESSAMENTO DE TEXTO OCR ===
def post_process_ocr_text(text: str) -> str:
    """
    Melhora o texto extraído por OCR corrigindo problemas comuns:
    - Separa palavras em maiúsculas coladas
    - Corrige caracteres mal interpretados
    - Melhora espaçamento
    """
    if not text:
        return text
    
    # Dicionário de correções comuns de OCR
    ocr_corrections = {
        r'\baS\b': 'às',
        r'\bde[Ss]aide\b': 'de Saúde',
        r'\bTeI\b': 'Tel',
        r'\bEsiado\b': 'Estado',
        r'\bGOVERNODOE[Ss]IADO\b': 'GOVERNO DO ESTADO',
    }
    
    # Aplicar correções de caracteres
    corrected_text = text
    for pattern, replacement in ocr_corrections.items():
        corrected_text = re.sub(pattern, replacement, corrected_text, flags=re.IGNORECASE)
    
    # Separar palavras em maiúsculas coladas (ex: JOAOSILVA -> JOAO SILVA)
    # Procura por padrões onde uma palavra termina e outra começa
    def separate_uppercase_words(match):
        word = match.group(0)
        # Adiciona espaço antes de cada letra maiúscula que segue uma minúscula ou outra maiúscula seguida de minúscula
        separated = re.sub(r'([a-z])([A-Z])', r'\1 \2', word)
        # Adiciona espaço entre sequências de maiúsculas e uma palavra que começa com maiúscula
        separated = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', separated)
        return separated
    
    # Separar nomes próprios colados (sequências longas de maiúsculas)
    # Identifica palavras com mais de 15 caracteres em maiúsculas sem espaços
    corrected_text = re.sub(r'\b[A-ZÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞ]{15,}\b', 
                           lambda m: separate_long_uppercase(m.group(0)), 
                           corrected_text)
    
    return corrected_text

def separate_long_uppercase(word: str) -> str:
    """
    Separa palavras longas em maiúsculas usando heurísticas.
    Ex: MARIAJOSE -> MARIA JOSE
    """
    if len(word) < 15:
        return word
    
    # Lista de nomes comuns brasileiros para ajudar na separação
    common_names = [
        'MARIA', 'JOSE', 'JOAO', 'ANA', 'ANTONIO', 'FRANCISCO', 'CARLOS', 'PAULO',
        'PEDRO', 'LUCAS', 'LUIZ', 'MARCOS', 'LUIS', 'GABRIEL', 'RAFAEL', 'DANIEL',
        'MARCELO', 'BRUNO', 'RODRIGO', 'FELIPE', 'GUSTAVO', 'ANDRE', 'FERNANDO',
        'FABIO', 'LEONARDO', 'RICARDO', 'DIEGO', 'JULIO', 'CESAR', 'ROBERTO',
        'JULIANA', 'MARIANA', 'FERNANDA', 'PATRICIA', 'ALINE', 'JULIANE', 'CARLA',
        'CAMILA', 'AMANDA', 'BRUNA', 'JESSICA', 'LETICIA', 'VANESSA', 'CRISTINA',
        'SILVA', 'SANTOS', 'OLIVEIRA', 'SOUZA', 'RODRIGUES', 'FERREIRA', 'ALVES',
        'PEREIRA', 'LIMA', 'GOMES', 'COSTA', 'RIBEIRO', 'MARTINS', 'CARVALHO',
        'ROCHA', 'ALMEIDA', 'LOPES', 'SOARES', 'FERNANDES', 'VIEIRA', 'BARBOSA',
        'ARAUJO', 'CASTRO', 'CARDOSO', 'NASCIMENTO', 'REIS', 'MOREIRA', 'PINTO',
        'RUA', 'AVENIDA', 'PRACA', 'TRAVESSA', 'ALAMEDA', 'RODOVIA',
        'MATERNIDADE', 'HOSPITAL', 'CLINICA', 'CENTRO', 'UNIDADE', 'POSTO',
        'FREI', 'SANTA', 'SANTO', 'SAO', 'NOSSA', 'SENHORA', 'BOM', 'BOA'
    ]
    
    result = []
    remaining = word
    
    while remaining:
        found = False
        # Tenta encontrar um nome comum no início da string
        for name in sorted(common_names, key=len, reverse=True):
            if remaining.startswith(name) and len(name) >= 3:
                result.append(name)
                remaining = remaining[len(name):]
                found = True
                break
        
        if not found:
            # Se não encontrou, pega os próximos 3-6 caracteres como uma palavra
            # Tenta encontrar um ponto de quebra natural
            chunk_size = min(6, len(remaining))
            if len(remaining) > 6:
                # Procura por uma sequência de vogal+consoante como ponto de quebra
                for i in range(3, min(8, len(remaining))):
                    if i < len(remaining) - 1:
                        if remaining[i] in 'AEIOU' and remaining[i+1] not in 'AEIOU':
                            chunk_size = i + 1
                            break
            
            result.append(remaining[:chunk_size])
            remaining = remaining[chunk_size:]
    
    return ' '.join(result)

# === ADIÇÃO 4: VALIDAÇÃO DE DADOS ===
def validar_cpf(cpf: str) -> bool:
    """Valida CPF usando algoritmo de dígito verificador."""
    cpf_digits = so_digitos(cpf)
    if len(cpf_digits) != 11 or cpf_digits == cpf_digits[0] * 11:
        return False
    
    # Validar primeiro dígito
    soma = sum(int(cpf_digits[i]) * (10 - i) for i in range(9))
    digito1 = 11 - (soma % 11)
    digito1 = 0 if digito1 > 9 else digito1
    
    if int(cpf_digits[9]) != digito1:
        return False
    
    # Validar segundo dígito
    soma = sum(int(cpf_digits[i]) * (11 - i) for i in range(10))
    digito2 = 11 - (soma % 11)
    digito2 = 0 if digito2 > 9 else digito2
    
    return int(cpf_digits[10]) == digito2

def validar_cns(cns: str) -> bool:
    """Valida Cartão Nacional de Saúde (CNS)."""
    cns_digits = so_digitos(cns)
    if len(cns_digits) != 15:
        return False
    
    # CNS começando com 1 ou 2
    if cns_digits[0] in ['1', '2']:
        soma = sum(int(cns_digits[i]) * (15 - i) for i in range(15))
        return soma % 11 == 0
    
    # CNS começando com 7, 8 ou 9
    if cns_digits[0] in ['7', '8', '9']:
        soma = sum(int(cns_digits[i]) * (15 - i) for i in range(15))
        return soma % 11 == 0
    
    return False

def validar_cep(cep: str) -> bool:
    """Valida formato de CEP."""
    cep_digits = so_digitos(cep)
    return len(cep_digits) == 8

def formatar_cpf(cpf: str) -> str:
    """Formata CPF no padrão XXX.XXX.XXX-XX."""
    digits = so_digitos(cpf)
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    return cpf

def formatar_cep(cep: str) -> str:
    """Formata CEP no padrão XXXXX-XXX."""
    digits = so_digitos(cep)
    if len(digits) == 8:
        return f"{digits[:5]}-{digits[5:]}"
    return cep

def formatar_telefone(telefone: str) -> str:
    """Formata telefone no padrão (XX) XXXXX-XXXX ou (XX) XXXX-XXXX."""
    digits = so_digitos(telefone)
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    elif len(digits) == 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    return telefone

# === ADIÇÃO 5: FORMATAÇÃO DO TEXTO DE DEBUG ===
def formatar_texto_debug(text: str) -> str:
    """Formata o texto extraído para melhor legibilidade."""
    if not text:
        return "Nenhum texto extraído."
    
    # Adiciona quebras de linha após campos comuns
    formatted = text
    
    # Lista de padrões que indicam início de nova seção
    section_markers = [
        'Identificacao do Estabelecimento',
        'Identificacao do Paciente',
        'Nome do Paciente',
        'Data de Nasc',
        'Endereco Residencial',
        'Justificativa da Internacao',
        'Diagnostico Inicial',
        'Procedimento Solicitado',
        'AUTORIZACAO'
    ]
    
    for marker in section_markers:
        formatted = formatted.replace(marker, f'\n\n{marker}')
    
    # Adiciona quebra após campos específicos
    formatted = re.sub(r'(CNS\s+\d+)', r'\1\n', formatted)
    formatted = re.sub(r'(CPF\s+[\d.-]+)', r'\1\n', formatted)
    formatted = re.sub(r'(CEP\s+[\d.-]+)', r'\1\n', formatted)
    formatted = re.sub(r'(Telefone[^)]+\))', r'\1\n', formatted)
    
    # Remove múltiplas quebras de linha
    formatted = re.sub(r'\n{3,}', '\n\n', formatted)
    
    return formatted.strip()

# --- MOTORES DE ANÁLISE (A BASE ESTÁVEL) ---
def parse_pdf_text(full_text: str):
    data = {}
    patterns = { "nome_paciente": r"Nome do Paciente\s+([A-ZÀ-ÿ\s]+?)\s+CNS", "cartao_sus": r"CNS\s+(\d{15})\s+", "nome_genitora": r"Nome da Mãe\s+([A-ZÀ-ÿ\s]+?)\s+Endereço Residencial", "data_nascimento": r"Data de Nasc\s+([\d/]+)\s+Sexo", "sexo": r"Sexo\s+(Feminino|Masculino)\s+Raça/cor", "raca": r"Raça/cor\s+([A-ZÀ-ÿ]+)\s+Nome do Responsável", "telefone_paciente": r"Telefone de Contato\s+([()\d\s-]+?)\s+Telefone Celular", "prontuario": r"Núm\. Prontuário\s+(\d+)\s+Telefone de Contato", "endereco_completo": r"Endereço Residencial \(Rua, Av etc\)\s+(.*?)\s+CPF", "municipio_referencia": r"Municipio de Referência\s+([A-ZÀ-ÿ\s]+?)\s+Cód\. IBGE", "uf": r"UF\s+([A-Z]{2})\s+CEP", "cep": r"CEP\s+([\d.-]+?)\s+Diretor Clinico", "diagnostico": r"Diagnóstico Inicial\s+(.*?)\s+CID 10 Principal", "cpf": r"CPF\s+([\d.-]+)\s+Municipio", }
    for field, pattern in patterns.items():
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match: data[field] = limpar_texto(match.group(1))
    if data.get("cartao_sus"): data["cartao_sus"] = so_digitos(data["cartao_sus"])
    if data.get("cep"): data["cep"] = so_digitos(data["cep"])
    if data.get("cpf"): data["cpf"] = so_digitos(data["cpf"])
    if data.get("telefone_paciente"): data["telefone_paciente"] = so_digitos(data["telefone_paciente"])
    return data

def parse_ocr_text(full_text: str):
    data = {}
    patterns = { "nome_paciente": r"Paciente\s*([A-Z\s]+?)\s*CNS", "cartao_sus": r"CNS\s*(\d{15})", "nome_genitora": r"Mae\s*([A-Z\s]+?)\s*(Feminino|Endereco)", "data_nascimento": r"Nasc\s*([\d/]+)", "sexo": r"(Feminino|Masculino)", "raca": r"Raca/cor\s*([A-Z]+)", "telefone_paciente": r"\((\d{2})\)\s?(\d{4,5}-?\d{4})", "prontuario": r"Prontuario\s*(\d+)", "diagnostico": r"Diagnostico\s*Inicial\s*(.*?)\s*CID", "cpf": r"CPF\s*([\d.-]+)", "endereco_completo": r"RUA\s*([A-Z\s,\d]+)", "municipio_referencia": r"Municipio\s*de\s*Referencia\s*([A-Z\s]+)", "uf": r"UF\s*([A-Z]{2})", "cep": r"CEP\s*([\d.-]+)", }
    for field, pattern in patterns.items():
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            value = next((g for g in match.groups() if g is not None and not g.lower() in ['feminino', 'endereco']), None)
            if value: data[field] = limpar_texto(value)
    if data.get("cartao_sus"): data["cartao_sus"] = so_digitos(data["cartao_sus"])
    if data.get("cep"): data["cep"] = so_digitos(data["cep"])
    if data.get("cpf"): data["cpf"] = so_digitos(data["cpf"])
    if data.get("telefone_paciente"): data["telefone_paciente"] = so_digitos(data["telefone_paciente"])
    return data

# --- PRÉ-PROCESSAMENTO E EXTRAÇÃO (COM AS NOVAS ADIÇÕES) ---
@st.cache_resource
def get_ocr_model():
    return RapidOCR()

def deskew(image: np.ndarray) -> np.ndarray:
    """Função para corrigir a inclinação da imagem."""
    coords = np.column_stack(np.where(image < 255))
    if len(coords) == 0:
        return image
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return rotated

def preprocess_image_for_ocr(image_bytes: bytes) -> bytes:
    """Aplica o pré-processamento completo, incluindo as novas adições."""
    try:
        pil_img = Image.open(io.BytesIO(image_bytes))
        # Autorotação
        try:
            for orientation in ExifTags.TAGS.keys():
                if ExifTags.TAGS[orientation] == 'Orientation': break
            exif = dict(pil_img._getexif().items())
            if exif[orientation] == 3: pil_img = pil_img.rotate(180, expand=True)
            elif exif[orientation] == 6: pil_img = pil_img.rotate(270, expand=True)
            elif exif[orientation] == 8: pil_img = pil_img.rotate(90, expand=True)
        except (AttributeError, KeyError, IndexError): pass
        
        img_array = np.array(pil_img.convert('RGB'))
        gray_img = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        
        # === ADIÇÃO 1: REMOÇÃO DE RUÍDO ===
        denoised_img = cv2.fastNlMeansDenoising(gray_img, None, 10, 7, 21)

        # === ADIÇÃO 2: CORREÇÃO DE INCLINAÇÃO (DESKEW) ===
        deskewed_img = deskew(denoised_img)

        # Binarização Adaptativa (que já tínhamos)
        processed_img = cv2.adaptiveThreshold(deskewed_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 15)
        
        _, buffer = cv2.imencode('.png', processed_img)
        return buffer.tobytes()
    except Exception:
        return image_bytes

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return " ".join(page.get_text(sort=True) for page in doc)

def extract_text_from_image(image_bytes: bytes) -> str:
    processed_bytes = preprocess_image_for_ocr(image_bytes)
    ocr = get_ocr_model()
    result, _ = ocr(processed_bytes)
    if not result: return ""
    full_text = "".join([item[1] for item in result])
    full_text = re.sub(r"([A-Z][a-z]+)", r" \1", full_text)
    full_text = re.sub(r"([A-Z]{2,})", r" \1", full_text)
    full_text = re.sub(r'\s+', ' ', full_text).strip()
    
    # === APLICAR PÓS-PROCESSAMENTO ===
    full_text = post_process_ocr_text(full_text)
    
    return full_text

# --- LÓGICA PRINCIPAL DO APLICATIVO ---
if "dados" not in st.session_state: st.session_state.dados = {}
if "full_text_debug" not in st.session_state: st.session_state.full_text_debug = ""
if "validacoes" not in st.session_state: st.session_state.validacoes = {}

st.title("Analisador de Laudo AIH")
st.markdown("---")

uploaded = st.file_uploader("📤 Carregar Laudo (PDF ou Imagem)", type=["pdf", "png", "jpg", "jpeg"])

if uploaded:
    with st.spinner("🔍 Analisando documento..."):
        try:
            file_bytes = uploaded.read()
            
            if "pdf" in uploaded.type:
                raw_text = extract_text_from_pdf(file_bytes)
                extracted_data = parse_pdf_text(raw_text)
            else:
                raw_text = extract_text_from_image(file_bytes)
                extracted_data = parse_ocr_text(raw_text)
            
            st.session_state.full_text_debug = raw_text
            st.session_state.dados = extracted_data
            
            # === ADIÇÃO 6: VALIDAR DADOS EXTRAÍDOS ===
            validacoes = {}
            if extracted_data.get("cpf"):
                validacoes["cpf"] = validar_cpf(extracted_data["cpf"])
            if extracted_data.get("cartao_sus"):
                validacoes["cns"] = validar_cns(extracted_data["cartao_sus"])
            if extracted_data.get("cep"):
                validacoes["cep"] = validar_cep(extracted_data["cep"])
            st.session_state.validacoes = validacoes

            if any(extracted_data.values()):
                st.success("✅ Documento analisado com sucesso!")
                
                # Mostrar avisos de validação
                if validacoes:
                    avisos = []
                    if "cpf" in validacoes and not validacoes["cpf"]:
                        avisos.append("⚠️ CPF pode estar incorreto")
                    if "cns" in validacoes and not validacoes["cns"]:
                        avisos.append("⚠️ CNS pode estar incorreto")
                    if "cep" in validacoes and not validacoes["cep"]:
                        avisos.append("⚠️ CEP pode estar incorreto")
                    
                    if avisos:
                        st.warning(" | ".join(avisos))
            else:
                st.warning("⚠️ Arquivo lido, mas nenhum dado foi extraído. Verifique o texto de debug.")
        except Exception as e:
            st.error("Ocorreu um erro crítico ao processar o arquivo.")
            st.session_state.full_text_debug = traceback.format_exc()

# --- INTERFACE DO FORMULÁRIO ---
def get_value(field, default=""):
    return st.session_state.dados.get(field, default)

def get_validation_icon(field):
    """Retorna ícone de validação para o campo."""
    validacoes = st.session_state.get("validacoes", {})
    if field in validacoes:
        return "✅" if validacoes[field] else "⚠️"
    return ""

st.markdown("---")
st.markdown("### 👤 Dados do Paciente")

col1, col2 = st.columns(2)
with col1: 
    st.text_input("Nome do Paciente", get_value("nome_paciente"), key="nome_input")
with col2: 
    st.text_input("Nome da Mãe", get_value("nome_genitora"), key="mae_input")

col3, col4 = st.columns(2)
with col3:
    cpf_val = get_value("cpf")
    cpf_label = f"CPF {get_validation_icon('cpf')}"
    st.text_input(cpf_label, formatar_cpf(cpf_val) if cpf_val else "", key="cpf_input")
with col4:
    cns_val = get_value("cartao_sus")
    cns_label = f"Cartão SUS {get_validation_icon('cns')}"
    st.text_input(cns_label, cns_val, key="cns_input")

col5, col6, col7 = st.columns(3)
with col5:
    st.text_input("Data de Nascimento", get_value("data_nascimento"), key="data_nasc_input")
with col6:
    st.text_input("Sexo", get_value("sexo"), key="sexo_input")
with col7:
    st.text_input("Raça/Cor", get_value("raca"), key="raca_input")

st.text_input("Prontuário", get_value("prontuario"), key="prontuario_input")

st.markdown("### 📍 Endereço e Contato")
st.text_input("Endereço Completo", get_value("endereco_completo"), key="endereco_input")

col8, col9 = st.columns(2)
with col8:
    st.text_input("Município", get_value("municipio_referencia"), key="municipio_input")
with col9:
    st.text_input("UF", get_value("uf"), key="uf_input")

col10, col11 = st.columns(2)
with col10:
    cep_val = get_value("cep")
    cep_label = f"CEP {get_validation_icon('cep')}"
    st.text_input(cep_label, formatar_cep(cep_val) if cep_val else "", key="cep_input")
with col11:
    tel_val = get_value("telefone_paciente")
    st.text_input("Telefone", formatar_telefone(tel_val) if tel_val else "", key="telefone_input")

st.markdown("### 🏥 Informações Clínicas")
st.text_area("Diagnóstico", get_value("diagnostico"), height=100, key="diagnostico_input")

st.markdown("---")

# === ADIÇÃO 7: BOTÕES DE AÇÃO ===
col_btn1, col_btn2 = st.columns(2)
with col_btn1:
    if st.button("📋 Copiar Dados (JSON)", use_container_width=True):
        import json
        dados_json = json.dumps(st.session_state.dados, ensure_ascii=False, indent=2)
        st.code(dados_json, language="json")
        st.info("💡 Use Ctrl+A e Ctrl+C para copiar")

with col_btn2:
    if st.button("🔄 Limpar Formulário", use_container_width=True):
        st.session_state.dados = {}
        st.session_state.full_text_debug = ""
        st.session_state.validacoes = {}
        st.rerun()

with st.expander("🔍 Ver texto completo extraído (debug)"):
    texto_formatado = formatar_texto_debug(st.session_state.get("full_text_debug", ""))
    st.code(texto_formatado, language="text")
    
    if st.session_state.get("full_text_debug"):
        st.download_button(
            label="💾 Baixar texto extraído",
            data=st.session_state.full_text_debug,
            file_name="texto_extraido.txt",
            mime="text/plain"
        )


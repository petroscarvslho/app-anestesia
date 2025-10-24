import io
import json
import re
from datetime import datetime

import fitz  # PyMuPDF
import streamlit as st
from PyPDFForm.wrapper import PdfWrapper

# =========================
# Config / Constantes
# =========================
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="wide")
st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.caption("Envie a Ficha AIH (PDF) para pré-preencher campos marcados com 🔵 AIH. Você pode editar tudo antes de gerar a ficha final.")

HEMOBA_TEMPLATE_PATH = "modelo_hemo.pdf"

# OCR de foto desabilitado por padrão (evita queda do app no Streamlit Cloud)
ENABLE_IMAGE_OCR = False  # mude para True quando prepararmos o requirements com OCR

# Telefones padrão por unidade
UNIDADES = {
    "Maternidade Frei Justo Venture": "(75) 3331-9400",
    "Hospital Regional da Chapada Diamantina": "",
}

# Campos que tentamos extrair da AIH
AIH_FIELDS = [
    "nome_paciente",
    "nome_genitora",
    "cartao_sus",
    "data_nascimento",
    "sexo",
    "raca",
    "telefone_paciente",
    "prontuario",
    "endereco_completo",
    "municipio_referencia",
    "uf",
    "cep",
]

# =========================
# Utilitários
# =========================
def limpar_nome(texto: str) -> str:
    if not texto:
        return ""
    # remove rótulos óbvios que às vezes “grudam”
    texto = re.sub(r"(Nome do Estabelecimento Solicitante)", "", texto, flags=re.I)
    palavras = re.findall(r"[A-Za-zÀ-ÿ\s]+", texto)
    return " ".join(p.strip() for p in palavras if p.strip()).strip()

def limpar_numeros(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")

def normalizar_uf(t: str) -> str:
    t = (t or "").upper().strip()
    if re.fullmatch(r"[A-Z]{2}", t):
        return t
    return ""

def normalizar_cep(t: str) -> str:
    nums = limpar_numeros(t)
    # aceita 8 dígitos
    if len(nums) == 8:
        return f"{nums[:5]}-{nums[5:]}"
    return ""

def normalizar_cns(t: str) -> str:
    nums = limpar_numeros(t)
    return nums[:15] if len(nums) >= 15 else nums

def normalizar_sexo(t: str) -> str:
    t = (t or "").strip().lower()
    if "fem" in t:
        return "Feminino"
    if "masc" in t:
        return "Masculino"
    return ""

def normalizar_raca(t: str) -> str:
    t = (t or "").strip().upper()
    for k in ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA", "INDIGENA"]:
        if k in t:
            return "INDÍGENA" if "INDIG" in k else k
    return ""

def indicador(valor, origem_aih: bool):
    # Bolinha cinza se vazio; azul se veio da AIH; sem ícone se digitado manualmente
    if not valor:
        return "⚪️ "
    return "🔵 " if origem_aih else ""

# =========================
# Parsing de PDF (passo “A”: regex no texto bruto)
# =========================
def parse_text_pass_A(full_text: str) -> dict:
    res = {}

    # CNS (15 dígitos)
    m = re.search(r"\b(\d{15})\b", full_text.replace(" ", ""))
    if m:
        res["cartao_sus"] = m.group(1)

    # Datas dd/mm/aaaa
    m = re.search(r"(\d{2}/\d{2}/\d{4})", full_text)
    if m:
        res["data_nascimento"] = m.group(1)

    # Sexo
    m = re.search(r"Sexo\s*(Feminino|Masculino)", full_text, flags=re.I)
    if m:
        res["sexo"] = m.group(1)

    # Raça/cor
    m = re.search(r"Ra[cç]a/?Cor\s*(BRANCA|PRETA|PARDA|AMARELA|IND[IÍ]GENA)", full_text, flags=re.I)
    if m:
        res["raca"] = m.group(1).upper()

    # Telefone
    m = re.search(r"(\(?\d{2}\)?\s?\d{4,5}-\d{4})", full_text)
    if m:
        res["telefone_paciente"] = m.group(1)

    # UF
    m = re.search(r"\b(UF)\s*\n([A-Z]{2})\b", full_text)
    if m:
        res["uf"] = m.group(2)

    # CEP
    m = re.search(r"\bCEP\b.*?\n([0-9.\- ]{8,10})", full_text)
    if m:
        res["cep"] = m.group(1)

    # Município de Referência
    m = re.search(r"Munic[ií]pio de Refer[êe]ncia\s*\n([A-ZÀ-ÿ \-]+)", full_text, flags=re.I)
    if m:
        res["municipio_referencia"] = m.group(1).strip()

    # Nome do Paciente e Nome da Mãe (tentativa simples)
    m = re.search(r"Nome do Paciente\s*\n([^\n]+)", full_text, flags=re.I)
    if m:
        res["nome_paciente"] = m.group(1).strip()
    m = re.search(r"Nome da M[ãa]e\s*\n([^\n]+)", full_text, flags=re.I)
    if m:
        res["nome_genitora"] = m.group(1).strip()

    # Prontuário
    m = re.search(r"N[úu]m\.\s*Prontu[áa]rio\s*\n([0-9A-Za-z\-./]+)", full_text, flags=re.I)
    if m:
        res["prontuario"] = m.group(1).strip()

    # Endereço Residencial / Endereço completo
    m = re.search(r"Endere[cç]o\s*(?:Residencial.*?|completo)\s*\n([^\n]+)", full_text, flags=re.I)
    if m:
        res["endereco_completo"] = m.group(1).strip()

    return res

# =========================
# Parsing de PDF (passo “B”: vizinho à direita/abaixo via blocks)
# =========================
def text_blocks(page):
    blocks = []
    for b in page.get_text("blocks"):
        if len(b) >= 5:
            x0, y0, x1, y1, txt = b[0], b[1], b[2], b[3], (b[4] or "").strip()
            if txt:
                blocks.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": txt})
    # ordenar por linha (y) depois por x
    blocks.sort(key=lambda z: (round(z["y0"], 1), z["x0"]))
    return blocks

def find_value_near_label(blocks, label_patterns):
    """Encontra o bloco de valor à direita (mesma linha) ou logo abaixo do label."""
    label_idx = None
    for i, b in enumerate(blocks):
        for pat in label_patterns:
            if re.search(pat, b["text"], flags=re.I):
                label_idx = i
                break
        if label_idx is not None:
            break
    if label_idx is None:
        return ""

    L = blocks[label_idx]
    # candidatos na mesma linha, à direita
    same_row = [
        b for b in blocks
        if (b["y0"] <= L["y1"] and b["y1"] >= L["y0"]) and b["x0"] > L["x1"] + 2
    ]
    same_row.sort(key=lambda b: b["x0"])
    if same_row:
        val = same_row[0]["text"].strip()
        return val

    # fallback: primeiro bloco logo abaixo
    below = [b for b in blocks if b["y0"] > L["y1"] + 2]
    below.sort(key=lambda b: (b["y0"], b["x0"]))
    if below:
        return below[0]["text"].strip()
    return ""

def parse_blocks_pass_B(page) -> dict:
    blocks = text_blocks(page)
    result = {}

    def get(pats):  # helper interno
        return find_value_near_label(blocks, pats)

    MAP = {
        "nome_paciente": [r"\bNome do Paciente\b"],
        "nome_genitora": [r"\bNome da M[ãa]e\b"],
        "cartao_sus": [r"\bCNS\b", r"Cart[aã]o SUS"],
        "data_nascimento": [r"Data de Nasc", r"Data de Nascimento"],
        "sexo": [r"\bSexo\b"],
        "raca": [r"Ra[cç]a/?Cor"],
        "telefone_paciente": [r"Telefone (?:de Contato|Celular|do Paciente)"],
        "prontuario": [r"N[úu]m\.\s*Prontu[áa]rio"],
        "endereco_completo": [r"Endere[cç]o (?:Residencial.*|completo)"],
        "municipio_referencia": [r"Munic[ií]pio de Refer[êe]ncia"],
        "uf": [r"\bUF\b"],
        "cep": [r"\bCEP\b"],
    }

    for key, pats in MAP.items():
        txt = get(pats)
        if txt:
            result[key] = txt

    return result

# =========================
# Orquestrador de extração
# =========================
def extract_from_pdf(file_like) -> tuple[dict, str]:
    """Retorna (dados_normalizados, texto_bruto)"""
    doc = fitz.open(stream=file_like.read(), filetype="pdf")
    if len(doc) == 0:
        return {}, ""
    page = doc[0]
    raw_text = page.get_text("text")

    # Passo A (regex no texto)
    a = parse_text_pass_A(raw_text)

    # Passo B (vizinhança de rótulos)
    b = parse_blocks_pass_B(page)

    # Combina A <- B (B corrige A quando existir)
    combined = {**a, **b}

    # Normalizações finais
    norm = {}
    norm["nome_paciente"] = limpar_nome(combined.get("nome_paciente", ""))
    norm["nome_genitora"] = limpar_nome(combined.get("nome_genitora", ""))
    norm["cartao_sus"] = normalizar_cns(combined.get("cartao_sus", ""))
    norm["data_nascimento"] = combined.get("data_nascimento", "")
    norm["sexo"] = normalizar_sexo(combined.get("sexo", ""))
    norm["raca"] = normalizar_raca(combined.get("raca", ""))
    norm["telefone_paciente"] = combined.get("telefone_paciente", "")
    norm["prontuario"] = limpar_numeros(combined.get("prontuario", "")) or combined.get("prontuario", "")
    norm["endereco_completo"] = combined.get("endereco_completo", "")
    norm["municipio_referencia"] = (combined.get("municipio_referencia", "") or "").strip()
    norm["uf"] = normalizar_uf(combined.get("uf", ""))
    norm["cep"] = normalizar_cep(combined.get("cep", ""))

    return norm, raw_text

# =========================
# Preenchimento do PDF final
# =========================
def fill_hemoba_pdf(template_path, data):
    data_for_pdf = {k: ("" if v is None else str(v)) for k, v in data.items()}

    # radios
    for field in ["antecedente_transfusional", "antecedentes_obstetricos", "reacao_transfusional"]:
        sel = data.get(field)
        data_for_pdf[f"{field}s"] = (sel == "Sim")
        data_for_pdf[f"{field}n"] = (sel == "Não")

    # modalidade
    modal_map = {
        "Programada": "modalidade_transfusaop",
        "Rotina": "modalidade_transfusaor",
        "Urgência": "modalidade_transfusaou",
        "Emergência": "modalidade_transfusaoe",
    }
    sel_mod = data.get("modalidade_transfusao")
    for nome, pdf_field in modal_map.items():
        data_for_pdf[pdf_field] = (sel_mod == nome)

    # produtos (se existirem no seu template — já deixo compatível)
    for product in ["hema", "pfc", "plaquetas_prod", "crio"]:
        data_for_pdf[product] = bool(data.get(product))

    pdf_form = PdfWrapper(template_path)
    pdf_form.fill(data_for_pdf, flatten=False)
    return pdf_form.read()

# =========================
# Estado inicial
# =========================
if "auto_data" not in st.session_state:
    st.session_state.auto_data = {k: "" for k in AIH_FIELDS}
if "manual" not in st.session_state:
    st.session_state.manual = {}
if "raw_text" not in st.session_state:
    st.session_state.raw_text = ""

# =========================
# Upload (PDF preferido; imagem opcional desativada)
# =========================
with st.container(border=True):
    st.header("1) Enviar Ficha AIH (PDF)")
    up = st.file_uploader("Arraste o PDF ou a foto (JPG/PNG)", type=["pdf", "jpg", "jpeg", "png"])

    if up is not None:
        try:
            if up.type == "application/pdf":
                st.session_state.auto_data, st.session_state.raw_text = extract_from_pdf(up)
                st.success("Dados extraídos! Revise e complete abaixo.")
            else:
                if not ENABLE_IMAGE_OCR:
                    st.warning("Leitura de FOTO está desativada no servidor (OCR pesado). Envie o **PDF** da AIH por enquanto.")
                else:
                    st.info("OCR de foto ainda em preparação.")
        except Exception as e:
            st.error(f"Erro ao ler arquivo: {e}")

# =========================
# Formulário (sempre visível, 1 coluna – friendly p/ celular)
# =========================
with st.form("form_hemo", border=True):
    st.header("2) Revisar e completar formulário ↪︎")

    aih = st.session_state.auto_data  # valores auto (AIH)

    # --- Identificação do Paciente ---
    st.subheader("Identificação do Paciente")
    nome_paciente = st.text_input(f"{indicador(aih.get('nome_paciente'), True)}Nome do Paciente",
                                  value=aih.get("nome_paciente", ""))
    nome_mae = st.text_input(f"{indicador(aih.get('nome_genitora'), True)}Nome da Mãe",
                             value=aih.get("nome_genitora", ""))
    cns = st.text_input(f"{indicador(aih.get('cartao_sus'), True)}Cartão SUS (CNS)",
                        value=aih.get("cartao_sus", ""))
    dt_nasc = st.text_input(f"{indicador(aih.get('data_nascimento'), True)}Data de Nascimento (DD/MM/AAAA)",
                            value=aih.get("data_nascimento", ""))

    sexo = st.radio(f"{indicador(aih.get('sexo'), True)}Sexo", ["Feminino", "Masculino"], index=0 if aih.get("sexo")=="Feminino" else 1 if aih.get("sexo")=="Masculino" else 0)
    raca_opts = ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA"]
    raca_idx = raca_opts.index(aih["raca"]) if aih.get("raca") in raca_opts else 0
    raca = st.radio(f"{indicador(aih.get('raca'), True)}Raça/Cor", raca_opts, index=raca_idx)

    tel_pac = st.text_input(f"{indicador(aih.get('telefone_paciente'), True)}Telefone do Paciente",
                            value=aih.get("telefone_paciente", ""))

    prontuario = st.text_input(f"{indicador(aih.get('prontuario'), True)}Prontuário",
                               value=aih.get("prontuario", ""))

    # --- Endereço ---
    st.subheader("Endereço")
    end = st.text_input(f"{indicador(aih.get('endereco_completo'), True)}Endereço completo",
                        value=aih.get("endereco_completo", ""))
    mun = st.text_input(f"{indicador(aih.get('municipio_referencia'), True)}Município de referência",
                        value=aih.get("municipio_referencia", ""))
    uf = st.text_input(f"{indicador(aih.get('uf'), True)}UF", value=aih.get("uf", ""))
    cep = st.text_input(f"{indicador(aih.get('cep'), True)}CEP", value=aih.get("cep", ""))

    # --- Estabelecimento ---
    st.subheader("Estabelecimento (selecione)")
    unidade_nome = st.radio("🏥 Hospital / Unidade de saúde", list(UNIDADES.keys()),
                            index=0)
    unidade_tel_default = UNIDADES.get(unidade_nome, "")
    unidade_tel = st.text_input("☎️ Telefone da Unidade (padrão, pode ajustar)", value=unidade_tel_default)

    # --- Data e Hora ---
    st.subheader("Data e Hora")
    hoje = datetime.now()
    data_str_default = hoje.strftime("%Y/%m/%d")
    hora_str_default = hoje.strftime("%H:%M")
    data_now = st.text_input("📅 Data", value=data_str_default)
    hora_now = st.text_input("⏰ Hora", value=hora_str_default)

    # --- Dados clínicos (manuais) ---
    st.subheader("Dados clínicos (manuais)")
    diag = st.text_input("🩺 Diagnóstico")
    peso = st.text_input("⚖️ Peso (kg)")
    ant_transf = st.radio("🩸 Antecedente Transfusional?", ["Não", "Sim"], index=0)
    ant_obst = st.radio("👶 Antecedentes Obstétricos?", ["Não", "Sim"], index=0)
    modalidade = st.radio("✍️ Modalidade de Transfusão", ["Rotina", "Programada", "Urgência", "Emergência"], index=0)

    submitted = st.form_submit_button("Gerar PDF Final", type="primary")

    if submitted:
        final = {
            # automáticos revisados
            "nome_paciente": nome_paciente,
            "nome_genitora": nome_mae,
            "cartao_sus": cns,
            "data_nascimento": dt_nasc,
            "sexo": sexo,
            "raca": raca,
            "telefone_paciente": tel_pac,
            "prontuario": prontuario,
            "endereco_completo": end,
            "municipio_referencia": mun,
            "uf": uf,
            "cep": cep,
            # unidade
            "unidade_saude": unidade_nome,
            "telefone_unidade": unidade_tel,
            # data/hora
            "data": data_now,
            "hora": hora_now,
            # clínicos
            "diagnostico": diag,
            "peso": peso,
            "antecedente_transfusional": ant_transf,
            "antecedentes_obstetricos": ant_obst,
            "modalidade_transfusao": modalidade,
        }

        try:
            pdf_bytes = fill_hemoba_pdf(HEMOBA_TEMPLATE_PATH, final)
            st.success("PDF gerado com sucesso!")
            nome_arquivo = f"HEMOBA_{(final.get('nome_paciente') or 'paciente').replace(' ', '_')}.pdf"
            st.download_button("✔️ Baixar Ficha HEMOBA", data=pdf_bytes, file_name=nome_arquivo, mime="application/pdf")
        except Exception as e:
            st.error(f"Erro ao preencher PDF: {e}")

# =========================
# Debug / Logs
# =========================
with st.expander("🐵 Ver texto extraído e pares (debug)"):
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Texto bruto**")
        st.text(st.session_state.raw_text or "")
    with col2:
        st.markdown("**Pares chave→valor parseados (AIH)**")
        st.code(json.dumps(st.session_state.auto_data, ensure_ascii=False, indent=2), language="json")

    # Download JSONL e CSV
    # JSONL (um registro por upload — aqui geramos 1)
    jsonl = json.dumps({**st.session_state.auto_data, "timestamp": datetime.now().isoformat()}, ensure_ascii=False)
    st.download_button("Baixar .jsonl", data=jsonl.encode("utf-8"), file_name="extracoes.jsonl", mime="application/json")

    # CSV simples (cabecalho + linha)
    cab = ["arquivo"] + AIH_FIELDS + ["timestamp"]
    vals = ["upload"] + [st.session_state.auto_data.get(k, "") for k in AIH_FIELDS] + [datetime.now().isoformat()]
    csv_str = ",".join(cab) + "\n" + ",".join([str(v).replace(",", " ") for v in vals])
    st.download_button("Baixar .csv", data=csv_str.encode("utf-8"), file_name="extracoes.csv", mime="text/csv")
import streamlit as st
import fitz  # PyMuPDF
import re
from datetime import datetime, date, time

# Import compatível com PyPDFForm/pypdfform
try:
    from PyPDFForm.wrapper import PdfWrapper  # pacote antigo
except Exception:
    from pypdfform import PdfWrapper  # pacote novo

import json
from collections import OrderedDict

# -------------------- Config & CSS (mobile-first) --------------------
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="wide")

MOBILE_CSS = """
<style>
/* padding mais compacto no celular */
@media (max-width: 700px) {
  .block-container { padding: .6rem .6rem 5rem; }
}
/* evita zoom automático no iOS e amplia área de toque */
input, textarea, select { font-size: 16px !important; }
.stTextInput>div>div>input,
.stTextArea textarea,
.stSelectbox>div>div,
.stRadio>div { padding: .65rem .75rem; }

/* botões grandes ocupando largura total */
.stButton>button {
  width: 100%;
  padding: .9rem 1rem;
  border-radius: 12px;
}

/* rótulo com “ponto” de origem */
label { font-size: .98rem; }
</style>
"""
st.markdown(MOBILE_CSS, unsafe_allow_html=True)

st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.markdown("Envie a **Ficha AIH (PDF)** para pré-preencher os campos marcados com 🔵 AIH. Você pode editar tudo antes de gerar a ficha final.")

HEMOBA_TEMPLATE_PATH = "modelo_hemo.pdf"

# hospitais e telefone padrão
HOSPITAIS = OrderedDict({
    "Maternidade Frei Justo Venture": {"phone": "(75) 3331-9400"},
    "Hospital Regional da Chapada Diamantina": {"phone": "(75) 3331-0000"},  # ajuste se necessário
})

RACAS = ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA"]

LABELS = {
    "nome_paciente": ["Nome do Paciente"],
    "nome_genitora": ["Nome da Mãe", "Nome da Mae"],
    "cartao_sus": ["CNS", "Cartão SUS", "Cartao SUS"],
    "data_nascimento": ["Data de Nasc", "Data de Nascimento"],
    "sexo": ["Sexo"],
    "raca": ["Raça/Cor", "Raça Cor", "Raça", "Raca/Cor", "Raca"],
    "prontuario": ["Núm. Prontuário", "Num. Prontuario", "Prontuário", "Prontuario"],
    "endereco_completo": ["Endereço Residencial", "Endereco Residencial"],
    "municipio_referencia": ["Município de Referência", "Municipio de Referencia", "Municipio de Referência"],
    "uf": ["UF"],
    "cep": ["CEP"],
    "telefone_paciente": ["Telefone Celular", "Telefone de Contato"],
}
KNOWN_LABELS = set(x for arr in LABELS.values() for x in arr)

# -------------------- Helpers --------------------
def mark(label: str, filled: bool) -> str:
    return f"{'🔵' if filled else '⚪️'} {label}"

def init_state():
    if "form_values" not in st.session_state:
        st.session_state.form_values = {
            # Identificação
            "nome_paciente": "",
            "nome_genitora": "",
            "cartao_sus": "",
            "data_nascimento": "",
            "sexo": "",
            "raca": "",
            "telefone_paciente": "",
            "prontuario": "",
            # Endereço
            "endereco_completo": "",
            "municipio_referencia": "",
            "uf": "",
            "cep": "",
            # Estabelecimento
            "unidade_saude": list(HOSPITAIS.keys())[0],
            "telefone_unidade": HOSPITAIS[list(HOSPITAIS.keys())[0]]["phone"],
            "data": datetime.now().date().strftime("%d/%m/%Y"),
            "hora": datetime.now().time().strftime("%H:%M"),
            # Clínicos
            "diagnostico": "",
            "peso": "",
            "antecedente_transfusional": "Não",
            "antecedentes_obstetricos": "Não",
            "modalidade_transfusao": "Rotina",
        }
    if "aih_filled" not in st.session_state:
        st.session_state.aih_filled = {k: False for k in st.session_state.form_values.keys()}
    if "last_uploaded_name" not in st.session_state:
        st.session_state.last_uploaded_name = None
    if "last_extracted_pairs" not in st.session_state:
        st.session_state.last_extracted_pairs = {}
    if "last_raw_text" not in st.session_state:
        st.session_state.last_raw_text = ""

init_state()

# -------------------- Parsing da AIH --------------------
def value_after_label_window(text: str, label: str, pattern: str, max_chars: int = 200, flags=re.IGNORECASE):
    idx = text.lower().find(label.lower())
    if idx == -1:
        return ""
    window = text[idx + len(label): idx + len(label) + max_chars]
    m = re.search(pattern, window, flags)
    return (m.group(1 if m and m.groups() else 0).strip() if m else "").strip()

def best_line_after_label(text: str, label: str, max_lines: int = 6):
    lines = text.splitlines()
    L = None
    for i, ln in enumerate(lines):
        if label.lower() in ln.lower():
            L = i
            break
    if L is None:
        return ""
    for j in range(1, max_lines + 1):
        if L + j >= len(lines): break
        cand = lines[L + j].strip()
        if not cand: 
            continue
        if any(cand.lower().startswith(lbl.lower()) for lbl in KNOWN_LABELS):
            continue
        return cand
    return ""

def parse_pairs_from_text(full_text: str):
    pairs = {}
    # CNS
    pairs["cartao_sus"] = (
        value_after_label_window(full_text, "CNS", r"\b(\d{15})\b")
        or (re.search(r"\b(\d{15})\b", full_text) or [None]).group(1) if re.search(r"\b\d{15}\b", full_text) else ""
    )
    # Data nascimento
    pairs["data_nascimento"] = (
        value_after_label_window(full_text, "Data de Nasc", r"(\d{2}/\d{2}/\d{4})")
        or value_after_label_window(full_text, "Data de Nascimento", r"(\d{2}/\d{2}/\d{4})")
        or ""
    )
    # Sexo
    m = re.search(r"\b(Feminino|Masculino)\b", full_text, re.IGNORECASE)
    pairs["sexo"] = (m.group(1).capitalize() if m else "")
    # Raça/Cor
    m = re.search(r"\b(BRANCA|PRETA|PARDA|AMARELA|IND[IÍ]GENA)\b", full_text, re.IGNORECASE)
    pairs["raca"] = (m.group(1).upper() if m else "")
    # Prontuário perto do rótulo
    pairs["prontuario"] = (
        value_after_label_window(full_text, "Núm. Prontuário", r"\b(\d{3,12})\b")
        or value_after_label_window(full_text, "Prontuário", r"\b(\d{3,12})\b")
        or ""
    )
    # Município referência
    pairs["municipio_referencia"] = (
        best_line_after_label(full_text, "Município de Referência")
        or best_line_after_label(full_text, "Municipio de Referencia")
        or best_line_after_label(full_text, "Municipio de Referência")
        or ""
    )
    # UF
    pairs["uf"] = (value_after_label_window(full_text, "UF", r"\b([A-Z]{2})\b", max_chars=12) or ("BA" if " BA" in full_text else ""))
    # CEP
    cep = value_after_label_window(full_text, "CEP", r"(\d{2}\.?\d{3}-?\d{3})", max_chars=40)
    pairs["cep"] = cep.replace(".", "").replace("-", "") if cep else ""
    # Endereço
    cand = best_line_after_label(full_text, "Endereço Residencial")
    if cand and ("," in cand or re.search(r"\d", cand)):
        pairs["endereco_completo"] = cand
    else:
        m = re.search(r"^(POV|RUA|AV(?:\.|ENIDA)?|TRAVESSA|ALAMEDA|ESTRADA).+$", full_text, re.MULTILINE | re.IGNORECASE)
        pairs["endereco_completo"] = m.group(0).strip() if m else ""
    # Nome da mãe / paciente
    pairs["nome_genitora"] = best_line_after_label(full_text, "Nome da Mãe") or ""
    pairs["nome_paciente"] = best_line_after_label(full_text, "Nome do Paciente") or ""
    # Telefone do paciente
    m = re.search(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}", full_text)
    pairs["telefone_paciente"] = m.group(0) if m else ""
    return pairs

def extract_by_position(page):
    results = {}
    words = page.get_text("words")
    def right_text_of(label, width=260, height_pad=4):
        rects = page.search_for(label)
        if not rects: return ""
        r = rects[0]
        rx0, ry0, rx1, ry1 = r.x1 + 2, r.y0 - height_pad, r.x1 + width, r.y1 + height_pad
        chunk = [w[4] for w in words if (rx0 <= w[0] <= rx1 and ry0 <= w[1] <= ry1)]
        return " ".join(chunk).strip()
    results["nome_paciente"] = right_text_of("Nome do Paciente")
    results["nome_genitora"] = right_text_of("Nome da Mãe") or right_text_of("Nome da Mae")
    results["cartao_sus"] = re.sub(r"\D", "", right_text_of("CNS"))
    results["prontuario"] = re.sub(r"\D", "", right_text_of("Núm. Prontuário") or right_text_of("Prontuário"))
    return {k: v for k, v in results.items() if v}

def extract_data_from_aih(upload):
    try:
        mem = upload.read()
        doc = fitz.open(stream=mem, filetype="pdf")
        page = doc[0]
        raw_text = page.get_text("text")
        st.session_state.last_raw_text = raw_text

        pairs = parse_pairs_from_text(raw_text)
        pos_rescue = extract_by_position(page)
        for k, v in pos_rescue.items():
            if not pairs.get(k):
                pairs[k] = v

        st.session_state.last_extracted_pairs = pairs
        return pairs
    except Exception as e:
        st.error(f"Erro ao ler AIH: {e}")
        return {}

# -------------------- Preenchimento do PDF --------------------
def fill_hemoba_pdf(template_path, data):
    try:
        d = {k: ("" if v is None else str(v)) for k, v in data.items()}
        for field in ["antecedente_transfusional", "antecedentes_obstetricos"]:
            sel = d.get(field, "Não")
            d[f"{field}s"] = sel == "Sim"
            d[f"{field}n"] = sel == "Não"
        modalidades = {
            "Programada": "modalidade_transfusaop",
            "Rotina": "modalidade_transfusaor",
            "Urgência": "modalidade_transfusaou",
            "Emergência": "modalidade_transfusaoe",
        }
        for nome, pdf_field in modalidades.items():
            d[pdf_field] = (d.get("modalidade_transfusao") == nome)
        pdf_form = PdfWrapper(template_path)
        pdf_form.fill(d, flatten=False)
        return pdf_form.read()
    except Exception as e:
        st.error(f"Erro ao preencher PDF: {e}")
        raise

# -------------------- Upload + parser --------------------
with st.container(border=True):
    st.header("1) Enviar Ficha AIH (PDF)")
    uploaded_file = st.file_uploader("Drag and drop file here", type="pdf", label_visibility="collapsed")
    if uploaded_file is not None and st.session_state.last_uploaded_name != uploaded_file.name:
        with st.spinner("Extraindo dados da AIH..."):
            pairs = extract_data_from_aih(uploaded_file)
            if pairs:
                for k, v in pairs.items():
                    if k in st.session_state.form_values and v:
                        st.session_state.form_values[k] = v
                        st.session_state.aih_filled[k] = True
                st.success("Dados extraídos! Revise e complete abaixo.")
            else:
                st.warning("Não foi possível extrair dados desta AIH.")
        st.session_state.last_uploaded_name = uploaded_file.name

# -------------------- Formulário: 1 coluna (mobile) --------------------
with st.form("hemo_form"):
    st.header("2) Revisar e completar formulário ↪")
    fv, af = st.session_state.form_values, st.session_state.aih_filled

    st.subheader("Identificação do Paciente")
    fv["nome_paciente"]      = st.text_input(mark("Nome do Paciente", af["nome_paciente"]), value=fv["nome_paciente"])
    fv["nome_genitora"]      = st.text_input(mark("Nome da Mãe", af["nome_genitora"]), value=fv["nome_genitora"])
    fv["cartao_sus"]         = st.text_input(mark("Cartão SUS (CNS)", af["cartao_sus"]), value=fv["cartao_sus"])
    fv["data_nascimento"]    = st.text_input(mark("Data de Nascimento (DD/MM/AAAA)", af["data_nascimento"]), value=fv["data_nascimento"])

    sexo_opts = ["Feminino", "Masculino"]
    sx_index = sexo_opts.index(fv["sexo"]) if fv["sexo"] in sexo_opts else 0
    fv["sexo"]               = st.radio(mark("Sexo", af["sexo"]), sexo_opts, index=sx_index)

    raca_index = RACAS.index(fv["raca"]) if fv["raca"] in RACAS else (RACAS.index("PARDA") if fv["raca"]=="" else 0)
    fv["raca"]               = st.radio(mark("Raça/Cor", af["raca"]), RACAS, index=raca_index)

    fv["telefone_paciente"]  = st.text_input(mark("Telefone do Paciente", af["telefone_paciente"]), value=fv["telefone_paciente"])
    fv["prontuario"]         = st.text_input(mark("Prontuário", af["prontuario"]), value=fv["prontuario"])

    st.subheader("Endereço")
    fv["endereco_completo"]  = st.text_input(mark("Endereço completo", af["endereco_completo"]), value=fv["endereco_completo"])
    fv["municipio_referencia"] = st.text_input(mark("Município de referência", af["municipio_referencia"]), value=fv["municipio_referencia"])
    fv["uf"]                 = st.text_input(mark("UF", af["uf"]), value=fv["uf"])
    fv["cep"]                = st.text_input(mark("CEP", af["cep"]), value=fv["cep"])

    st.subheader("Estabelecimento (selecione)")
    hosp_list = list(HOSPITAIS.keys())
    fv["unidade_saude"]      = st.radio("🏥 Hospital / Unidade de saúde", hosp_list, index=hosp_list.index(fv["unidade_saude"]))
    fv["telefone_unidade"]   = HOSPITAIS[fv["unidade_saude"]]["phone"]
    st.text_input("📞 Telefone da Unidade (padrão, pode ajustar)", value=fv["telefone_unidade"], key="telefone_unidade")

    # Data/hora – controles próprios de data/hora ajudam no celular
    st.subheader("Data e Hora")
    try:
        today = datetime.now().date()
        now_t = datetime.now().time()
        d = st.date_input("📅 Data", value=today)
        t = st.time_input("⏰ Hora", value=now_t)
        fv["data"] = d.strftime("%d/%m/%Y")
        fv["hora"] = t.strftime("%H:%M")
    except Exception:
        # fallback se algo impedir (mantém texto)
        fv["data"] = st.text_input("📅 Data", value=fv["data"])
        fv["hora"] = st.text_input("⏰ Hora", value=fv["hora"])

    st.subheader("Dados clínicos (manuais)")
    fv["diagnostico"]           = st.text_input("🧬 Diagnóstico", value=fv["diagnostico"])
    fv["peso"]                  = st.text_input("⚖️ Peso (kg)", value=fv["peso"])
    fv["antecedente_transfusional"] = st.radio("🩸 Antecedente Transfusional?", ["Não", "Sim"],
                                               index=0 if fv["antecedente_transfusional"]=="Não" else 1)
    fv["antecedentes_obstetricos"]  = st.radio("🤰 Antecedentes Obstétricos?", ["Não", "Sim"],
                                               index=0 if fv["antecedentes_obstetricos"]=="Não" else 1)
    fv["modalidade_transfusao"] = st.radio("💉 Modalidade de Transfusão", ["Rotina","Programada","Urgência","Emergência"],
                                           index=["Rotina","Programada","Urgência","Emergência"].index(fv["modalidade_transfusao"]))

    submitted = st.form_submit_button("Gerar PDF Final", type="primary")
    if submitted:
        with st.spinner("Gerando o PDF..."):
            st.session_state.form_values = fv
            filled = fill_hemoba_pdf(HEMOBA_TEMPLATE_PATH, fv)
            st.success("PDF gerado com sucesso!")
            nome_out = (fv.get("nome_paciente") or "paciente").replace(" ", "_")
            st.download_button("✔️ Baixar Ficha HEMOBA", data=filled, file_name=f"HEMOBA_{nome_out}.pdf", mime="application/pdf")

# -------------------- Debug / Export --------------------
with st.expander("🐒 Ver texto extraído e pares (debug)"):
    col1, col2 = st.columns(2)
    with col1:
        st.text_area("Texto bruto", st.session_state.last_raw_text, height=420)
        if st.session_state.last_extracted_pairs:
            obj = {
                **st.session_state.last_extracted_pairs,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "arquivo": st.session_state.last_uploaded_name or "",
            }
            jsonl = json.dumps(obj, ensure_ascii=False) + "\n"
            st.download_button("Baixar .jsonl", data=jsonl.encode("utf-8"),
                               file_name="extracoes.jsonl", mime="application/json")
    with col2:
        st.code(json.dumps(st.session_state.last_extracted_pairs, ensure_ascii=False, indent=2), language="json")
        if st.session_state.last_extracted_pairs:
            header = ["arquivo","cartao_sus","cep","data_nascimento","endereco_completo","municipio_referencia",
                      "nome_genitora","nome_paciente","prontuario","raca","sexo","timestamp","uf"]
            obj = {
                "arquivo": st.session_state.last_uploaded_name or "",
                "cartao_sus": st.session_state.last_extracted_pairs.get("cartao_sus",""),
                "cep": st.session_state.last_extracted_pairs.get("cep",""),
                "data_nascimento": st.session_state.last_extracted_pairs.get("data_nascimento",""),
                "endereco_completo": st.session_state.last_extracted_pairs.get("endereco_completo",""),
                "municipio_referencia": st.session_state.last_extracted_pairs.get("municipio_referencia",""),
                "nome_genitora": st.session_state.last_extracted_pairs.get("nome_genitora",""),
                "nome_paciente": st.session_state.last_extracted_pairs.get("nome_paciente",""),
                "prontuario": st.session_state.last_extracted_pairs.get("prontuario",""),
                "raca": st.session_state.last_extracted_pairs.get("raca",""),
                "sexo": st.session_state.last_extracted_pairs.get("sexo",""),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "uf": st.session_state.last_extracted_pairs.get("uf",""),
            }
            row = ",".join([str(obj.get(h,"")).replace(",", " ") for h in header])
            csv_bytes = (",".join(header) + "\n" + row + "\n").encode("utf-8")
            st.download_button("Baixar .csv", data=csv_bytes, file_name="extracoes.csv", mime="text/csv")

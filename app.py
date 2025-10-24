import io
import re
import json
import fitz  # PyMuPDF
import base64
import pandas as pd
import numpy as np
import unicodedata
import streamlit as st
from datetime import datetime, date, time
from PyPDFForm.wrapper import PdfWrapper
from PIL import Image

# ====== CONFIG ======
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="centered")
st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.caption("Envie **PDF AIH** ou **foto** do laudo. Campos marcados com 🔵 vieram da AIH/OCR. Você pode revisar tudo.")

HEMOBA_TEMPLATE_PATH = "modelo_hemo.pdf"

HOSPITAIS = {
    "Maternidade Frei Justo Venture": "(75) 3331-9400",
    "Hospital Regional da Chapada Diamantina": "(75) 3331-9400",
}

# ====== ESTADO ======
if "form_values" not in st.session_state:
    st.session_state.form_values = {}
if "autofilled" not in st.session_state:
    st.session_state.autofilled = set()
if "raw_text" not in st.session_state:
    st.session_state.raw_text = ""
if "pairs" not in st.session_state:
    st.session_state.pairs = {}

# ====== UTILS ======
def norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s\-/().:]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s

def only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def looks_like_label(s: str) -> bool:
    n = norm(s)
    for syns, _ in LABELS:
        for lab in syns:
            if n.startswith(lab):
                return True
    return False

def is_name_like(s: str) -> bool:
    # evita capturar números como nome
    return bool(re.search(r"[A-Za-zÀ-ÿ]{2,}", s or ""))

def dotted(label: str, key: str) -> str:
    # coloca 🔵 no label se veio da AIH/OCR
    return ("🔵 " if key in st.session_state.autofilled else "⚪️ ") + label

# ====== PARSERS (robustos) ======
# mapa de rótulos -> chave do dicionário final (com sinônimos/fuzzy leves)
LABELS = [
    (("nome do paciente", "paciente"), "nome_paciente"),
    (("nome da mae", "nome da mãe", "mae", "mãe", "nome da genitora"), "nome_genitora"),
    (("cns", "cartao sus", "cartão sus", "cartao do sus", "cartão do sus"), "cartao_sus"),
    (("data de nasc", "data de nascimento", "dt nasc"), "data_nascimento"),
    (("sexo",), "sexo"),
    (("raca", "raça", "raca/cor", "raça/cor"), "raca"),
    (("municipio de referencia", "município de referência"), "municipio_referencia"),
    (("endereco residencial", "endereço residencial", "endereco completo", "endereço completo", "endereco", "endereço"), "endereco_completo"),
    (("prontuario", "nº prontuario", "num. prontuario", "número do prontuario", "núm. prontuário"), "prontuario"),
    (("telefone", "telefone celular", "telefone de contato", "tel", "telefone do paciente"), "telefone_paciente"),
    (("uf",), "uf"),
    (("cep",), "cep"),
    (("nome do estabelecimento solicitante", "estabelecimento solicitante"), "hospital_hint"),
    (("nome do estabelecimento executante", "estabelecimento executante"), "unidade_hint"),
]

RACAS = ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA", "INDIGENA"]

def parse_from_lines(lines: list[str]) -> dict:
    pairs = {}
    i = 0
    L = len(lines)

    def next_value(idx: int) -> str:
        j = idx + 1
        while j < L and (not lines[j].strip() or looks_like_label(lines[j])):
            j += 1
        return lines[j].strip() if j < L else ""

    for i in range(L):
        raw = lines[i].strip()
        n = norm(raw)
        for syns, key in LABELS:
            if any(n.startswith(s) for s in syns) or n in syns:
                val = next_value(i)
                # sanitizações por tipo
                if key in ("nome_paciente", "nome_genitora"):
                    if looks_like_label(val) or not is_name_like(val):
                        continue
                if key == "cartao_sus":
                    val = only_digits(val)[:15]
                if key == "prontuario":
                    val = only_digits(val)
                pairs[key] = val
                break

    # capturas globais, se faltou
    full_text = "\n".join(lines)

    if not pairs.get("cartao_sus"):
        m = re.search(r"\b(\d{15})\b", full_text.replace(" ", ""))
        if m: pairs["cartao_sus"] = m.group(1)

    if not pairs.get("data_nascimento"):
        m = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", full_text)
        if m: pairs["data_nascimento"] = m.group(1)

    if not pairs.get("sexo"):
        if re.search(r"\bfeminino\b", norm(full_text)): pairs["sexo"] = "Feminino"
        elif re.search(r"\bmasculino\b", norm(full_text)): pairs["sexo"] = "Masculino"

    if not pairs.get("raca"):
        for rac in RACAS:
            if rac.lower() in norm(full_text):
                pairs["raca"] = rac
                break

    if not pairs.get("telefone_paciente"):
        m = re.search(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}", full_text)
        if m: pairs["telefone_paciente"] = m.group(0)

    if not pairs.get("uf"):
        m = re.search(r"\bUF[:\s]*([A-Z]{2})\b", full_text, re.IGNORECASE)
        if m: pairs["uf"] = m.group(1).upper()

    if not pairs.get("cep"):
        m = re.search(r"\b\d{2}\.?\d{3}-?\d{3}\b", full_text)
        if m: pairs["cep"] = m.group(0)

    # limpeza final simples
    for k in ("nome_paciente", "nome_genitora"):
        if k in pairs:
            pairs[k] = re.sub(r"[^A-Za-zÀ-ÿ\s'-]", " ", pairs[k]).strip()
            pairs[k] = re.sub(r"\s+", " ", pairs[k])

    return pairs

# ====== OCR (imagens) ======
def ocr_image_to_text(file) -> str:
    # leitura com PIL -> numpy
    img = Image.open(file).convert("RGB")
    arr = np.array(img)

    # pré-processamento leve (cinza e leve binarização ajuda OCR)
    import cv2
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, h=15)
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    # OCR
    import easyocr
    reader = easyocr.Reader(["pt", "en"], gpu=False, verbose=False)
    result = reader.readtext(thr, detail=0, paragraph=True)
    text = "\n".join([r.strip() for r in result if r and r.strip()])
    return text

# ====== PDF (texto) ======
def pdf_first_page_text(file) -> str:
    doc = fitz.open(stream=file.read(), filetype="pdf")
    if len(doc) == 0:
        return ""
    page = doc[0]
    return page.get_text("text")

# ====== ORQUESTRADOR DE EXTRAÇÃO ======
def extract_pairs_from_file(uploaded) -> tuple[str, dict]:
    suffix = (uploaded.name or "").lower()
    raw_text = ""
    pairs = {}

    try:
        if suffix.endswith(".pdf"):
            raw_text = pdf_first_page_text(uploaded)
        else:
            # imagens
            raw_text = ocr_image_to_text(uploaded)
    except Exception as e:
        st.error(f"Erro ao ler arquivo: {e}")
        return "", {}

    lines = [ln for ln in raw_text.splitlines()]
    pairs = parse_from_lines(lines)
    return raw_text, pairs

# ====== PREENCHER PDF FINAL ======
def fill_hemoba_pdf(template_path, data):
    try:
        data_for_pdf = {k: ("" if v is None else str(v)) for k, v in data.items()}

        # checkboxes Sim/Não
        for field in ["antecedente_transfusional", "antecedentes_obstetricos", "reacao_transfusional"]:
            selection = data.get(field)
            data_for_pdf[f"{field}s"] = (selection == "Sim")
            data_for_pdf[f"{field}n"] = (selection == "Não")

        # modalidade
        modalidades = {
            "Programada": "modalidade_transfusaop",
            "Rotina": "modalidade_transfusaor",
            "Urgência": "modalidade_transfusaou",
            "Emergência": "modalidade_transfusaoe",
        }
        selected = data.get("modalidade_transfusao")
        for k, pdf_field in modalidades.items():
            data_for_pdf[pdf_field] = (k == selected)

        for product in ["hema", "pfc", "plaquetas_prod", "crio"]:
            data_for_pdf[product] = bool(data.get(product))

        pdf_form = PdfWrapper(template_path)
        pdf_form.fill(data_for_pdf, flatten=False)
        return pdf_form.read()
    except Exception as e:
        st.error(f"Erro ao preencher PDF: {e}")
        raise

# ====== UPLOAD ======
st.subheader("1) Enviar Ficha AIH (PDF) **ou** Foto")
uploaded = st.file_uploader("Arraste o PDF ou a foto (JPG/PNG)", type=["pdf", "jpg", "jpeg", "png"])

if uploaded:
    with st.spinner("Lendo e extraindo..."):
        raw_text, pairs = extract_pairs_from_file(uploaded)

    st.session_state.raw_text = raw_text
    st.session_state.pairs = pairs

    # marca como autofilled e salva nos form_values
    st.session_state.autofilled = set()
    for k, v in pairs.items():
        if v:
            st.session_state.form_values[k] = v
            st.session_state.autofilled.add(k)

    st.success("Dados extraídos! Revise e complete abaixo.")

# ====== FORM (sempre visível, 1 coluna, mobile) ======
st.subheader("2) Revisar e completar formulário ↪")

with st.form("hemo_form"):
    fv = st.session_state.form_values

    # ---------- Identificação ----------
    st.markdown("### Identificação do Paciente")

    fv["nome_paciente"] = st.text_input(dotted("Nome do Paciente", "nome_paciente"), value=fv.get("nome_paciente", ""))
    fv["nome_genitora"] = st.text_input(dotted("Nome da Mãe", "nome_genitora"), value=fv.get("nome_genitora", ""))

    fv["cartao_sus"] = st.text_input(dotted("Cartão SUS (CNS)", "cartao_sus"), value=fv.get("cartao_sus", ""))
    fv["data_nascimento"] = st.text_input(dotted("Data de Nascimento (DD/MM/AAAA)", "data_nascimento"), value=fv.get("data_nascimento", ""))

    # sexo
    sexo_default = 0 if fv.get("sexo", "") == "Feminino" else (1 if fv.get("sexo", "") == "Masculino" else 0)
    sexo_val = st.radio(dotted("Sexo", "sexo"), options=["Feminino", "Masculino"], index=sexo_default, horizontal=False)
    fv["sexo"] = sexo_val

    # raça/cor
    raca_opts = ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA"]
    r0 = fv.get("raca")
    r_idx = raca_opts.index(r0) if r0 in raca_opts else 2  # PARDA como padrão
    fv["raca"] = st.radio(dotted("Raça/Cor", "raca"), options=raca_opts, index=r_idx, horizontal=False)

    fv["telefone_paciente"] = st.text_input(dotted("Telefone do Paciente", "telefone_paciente"), value=fv.get("telefone_paciente", ""))

    fv["prontuario"] = st.text_input(dotted("Núm. Prontuário", "prontuario"), value=fv.get("prontuario", ""))

    # ---------- Endereço ----------
    st.markdown("### Endereço")
    fv["endereco_completo"] = st.text_input(dotted("Endereço completo", "endereco_completo"), value=fv.get("endereco_completo", ""))
    fv["municipio_referencia"] = st.text_input(dotted("Município de referência", "municipio_referencia"), value=fv.get("municipio_referencia", ""))
    fv["uf"] = st.text_input(dotted("UF", "uf"), value=fv.get("uf", "BA"))
    fv["cep"] = st.text_input(dotted("CEP", "cep"), value=fv.get("cep", ""))

    # ---------- Estabelecimento ----------
    st.markdown("### Estabelecimento (selecione)")
    hosp_opts = list(HOSPITAIS.keys())
    hosp_default = hosp_opts.index(fv.get("hospital", hosp_opts[0])) if fv.get("hospital") in hosp_opts else 0
    fv["hospital"] = st.radio("🏥 Hospital / Unidade de saúde", options=hosp_opts, index=hosp_default, horizontal=False)
    # telefone padrão (só preenche se vazio)
    if not fv.get("telefone_unidade"):
        fv["telefone_unidade"] = HOSPITAIS.get(fv["hospital"], "")

    fv["telefone_unidade"] = st.text_input("📞 Telefone da Unidade (padrão, pode ajustar)", value=fv.get("telefone_unidade", ""))

    # ---------- Data e Hora ----------
    st.markdown("### Data e Hora")
    today = date.today()
    now = datetime.now().time().replace(second=0, microsecond=0)
    fv["data"] = st.date_input("📅 Data", value=fv.get("data", today))
    fv["hora"] = st.time_input("⏰ Hora", value=fv.get("hora", now), step=60)

    # ---------- Dados clínicos (manuais) ----------
    st.markdown("### Dados clínicos (manuais)")
    fv["diagnostico"] = st.text_input("🩺 Diagnóstico", value=fv.get("diagnostico", ""))
    fv["peso"] = st.text_input("⚖️ Peso (kg)", value=fv.get("peso", ""))

    fv["antecedente_transfusional"] = st.radio("🩸 Antecedente Transfusional?", options=["Não", "Sim"], index=0 if fv.get("antecedente_transfusional") != "Sim" else 1, horizontal=False)
    fv["antecedentes_obstetricos"] = st.radio("🤰 Antecedentes Obstétricos?", options=["Não", "Sim"], index=0 if fv.get("antecedentes_obstetricos") != "Sim" else 1, horizontal=False)

    fv["modalidade_transfusao"] = st.radio("✍️ Modalidade de Transfusão", options=["Rotina", "Programada", "Urgência", "Emergência"],
                                           index=["Rotina","Programada","Urgência","Emergência"].index(fv.get("modalidade_transfusao", "Rotina")),
                                           horizontal=False)

    submitted = st.form_submit_button("Gerar PDF Final", type="primary")

# ====== AÇÃO DO SUBMIT ======
if submitted:
    final = {**st.session_state.form_values}
    # normaliza tipos para PDF
    final["data"] = final.get("data", date.today())
    final["hora"] = final.get("hora", datetime.now().time())
    final["data_str"] = final["data"].strftime("%d/%m/%Y") if isinstance(final["data"], date) else str(final["data"])
    final["hora_str"] = final["hora"].strftime("%H:%M") if isinstance(final["hora"], time) else str(final["hora"])

    # também guardo timestamp da extração
    final["timestamp"] = datetime.now().isoformat(timespec="seconds")

    try:
        pdf_bytes = fill_hemoba_pdf(HEMOBA_TEMPLATE_PATH, final)
        st.success("PDF gerado com sucesso!")
        nome = (final.get("nome_paciente") or "paciente").strip().replace(" ", "_")
        st.download_button("✔️ Baixar Ficha HEMOBA", data=pdf_bytes, file_name=f"HEMOBA_{nome}.pdf", mime="application/pdf")
    except Exception:
        pass

# ====== DEBUG + LOG ======
with st.expander("🐿️ Ver texto extraído e pares (debug)"):
    st.markdown("**Texto bruto**")
    st.text_area("", st.session_state.raw_text or "", height=260)
    st.markdown("**Pares chave→valor parseados (AIH/OCR)**")
    st.json(st.session_state.pairs or {})

    # downloads (JSON e CSV da linha atual)
    if st.session_state.pairs:
        row = {**st.session_state.pairs}
        row["arquivo"] = uploaded.name if uploaded else ""
        row["timestamp"] = datetime.now().isoformat(timespec="seconds")
        df = pd.DataFrame([row])
        st.download_button("Baixar .jsonl", data="\n".join(df.to_dict(orient="records")).encode("utf-8"),
                           file_name="extracoes.jsonl", mime="application/json")
        st.download_button("Baixar .csv", data=df.to_csv(index=False).encode("utf-8"),
                           file_name="extracoes.csv", mime="text/csv")
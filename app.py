# -*- coding: utf-8 -*-
"""
Gerador Automático de Ficha HEMOBA (mobile-first)
- Formulário sempre visível (single column)
- Campos vindos da AIH marcados com 🔵
- Extração robusta por RÓTULO (PDF via PyMuPDF)
- OCR de fotos opcional e à prova de falhas (EasyOCR). Se faltar modelo, o app continua.
- Preenche o PDF final com PyPDFForm
"""

import os
import io
import re
from datetime import datetime
from pathlib import Path

import streamlit as st
import fitz  # PyMuPDF
from PyPDFForm.wrapper import PdfWrapper

# ==== OCR opcional (não quebra se não existir) =================================
ENABLE_OCR = True
try:
    import easyocr  # type: ignore
    import numpy as np  # type: ignore
    from PIL import Image  # type: ignore
except Exception:
    ENABLE_OCR = False

# ==== Constantes de app ========================================================
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="wide")
HEMOBA_TEMPLATE_PATH = "modelo_hemo.pdf"

HOSPITAIS = {
    "Maternidade Frei Justo Venture": "(75) 3331-9400",
    "Hospital Regional da Chapada Diamantina": "(75) 3331-1900",
}

# ==== Estilos (mobile-first) ===================================================
st.markdown(
    """
<style>
/* largura confortável em mobile; bom também no desktop */
.block-container { max-width: 820px; }

/* títulos uniformes */
h1, h2, h3 { font-weight: 700; letter-spacing: .2px; }
h1 { font-size: 1.6rem; } h2 { font-size: 1.25rem; } h3 { font-size: 1.05rem; }

/* seções com “cartão” */
.section { padding: 1rem; border: 1px solid #eee; border-radius: 12px; margin-bottom: 1rem; background:#fff; }

/* radios verticais com respiro */
.stRadio > div { gap: .5rem }

/* rótulo com bolinha azul quando veio da AIH */
.label-dot::before {
  content: "•";
  color: #1f77ff;
  margin-right: .5rem;
}

/* inputs com ajuste de margem */
.stTextInput, .stDateInput, .stTimeInput { margin-top: .25rem; }
.help {font-size:.80rem; color:#667; margin:.4rem 0 0;}
</style>
""",
    unsafe_allow_html=True,
)

# ==== Helpers de limpeza/normalização =========================================
def limpar_nome(x: str | None) -> str:
    if not x:
        return ""
    return " ".join(re.findall(r"[A-Za-zÀ-ÿ]+", x)).strip()

def somente_digitos(x: str | None) -> str:
    return re.sub(r"\D", "", x or "")

def normalizar_campos(d: dict) -> dict:
    out = dict(d)

    # CNS (15 dígitos)
    cns = somente_digitos(out.get("cartao_sus"))
    out["cartao_sus"] = cns[:15] if cns else ""

    # Data de nascimento -> DD/MM/AAAA
    dt = out.get("data_nascimento", "")
    m = re.search(r"(\d{2})[^\d]?(\d{2})[^\d]?(\d{4})", dt)
    if m:
        out["data_nascimento"] = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"

    # Sexo
    sx = (out.get("sexo") or "").lower()
    if "fem" in sx:
        out["sexo"] = "Feminino"
    elif "masc" in sx:
        out["sexo"] = "Masculino"
    else:
        out["sexo"] = ""

    # Raça/Cor
    r = (out.get("raca") or "").upper()
    for opt in ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA", "INDIGENA"]:
        if opt in r:
            out["raca"] = "INDÍGENA" if "NDÍGEN" in opt else opt

    # UF
    uf = re.findall(r"\b[A-Z]{2}\b", out.get("uf") or "")
    out["uf"] = uf[0] if uf else out.get("uf", "")

    # CEP
    cep = re.search(r"\b\d{5}-?\d{3}\b", out.get("cep") or "")
    out["cep"] = cep.group(0) if cep else ""

    # Telefone (paciente)
    tel = re.search(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}", out.get("telefone_paciente") or "")
    out["telefone_paciente"] = tel.group(0) if tel else out.get("telefone_paciente", "")

    # Nomes
    for k in ["nome_paciente", "nome_genitora"]:
        out[k] = limpar_nome(out.get(k))

    return out

# ==== Mapa de rótulos -> campos internos ======================================
AIH_LABELS = {
    "Nome do Paciente": "nome_paciente",
    "Nome da Mãe": "nome_genitora",
    "CNS": "cartao_sus",
    "Cartão SUS": "cartao_sus",
    "Data de Nasc": "data_nascimento",
    "Sexo": "sexo",
    "Raça/cor": "raca",
    "Raça/Cor": "raca",
    "Núm. Prontuário": "prontuario",
    "Município de Referência": "municipio_referencia",
    "Município de referência": "municipio_referencia",
    "UF": "uf",
    "CEP": "cep",
    "Telefone de Contato": "telefone_paciente",
    "Endereço Residencial (Rua, Av etc)": "endereco_completo",
}

# ==== Extração por RÓTULO (PDF) ===============================================
def _overlap_y(a: fitz.Rect, b: fitz.Rect, tol: float = 3.0) -> bool:
    return not (b.y1 < a.y0 - tol or b.y0 > a.y1 + tol)

def extract_by_label(page: fitz.Page) -> dict:
    """
    Para cada rótulo (ex.: 'Nome do Paciente'), pega o texto à direita do retângulo
    do rótulo, na MESMA faixa de Y, e PARA antes do próximo rótulo que cruza a faixa.
    """
    results: dict[str, str] = {}

    # Todas as palavras da página
    words = page.get_text("words")  # [x0,y0,x1,y1,"word",...]
    # Todos os retângulos de rótulo encontrados
    label_rects: list[tuple[str, str, fitz.Rect]] = []
    for label, field in AIH_LABELS.items():
        rects = page.search_for(label)
        for r in rects:
            label_rects.append((label, field, r))

    # Ordena da esquerda para direita (ajuda a achar próximo rótulo corretamente)
    label_rects.sort(key=lambda t: (t[2].y0, t[2].x0))

    for i, (label, field, r) in enumerate(label_rects):
        # acha o “próximo rótulo” que cruza a mesma faixa de Y e está à direita
        stop_x = page.rect.x1
        for j, (_, _, r2) in enumerate(label_rects):
            if j == i:
                continue
            if r2.x0 > r.x1 and _overlap_y(r, r2):
                stop_x = min(stop_x, r2.x0)

        # pega palavras na mesma banda de Y e entre (r.x1, stop_x)
        band = [
            w for w in words
            if (r.y0 - 2) <= w[1] <= (r.y1 + 2) and (r.x1 + 1) <= w[0] <= (stop_x - 1)
        ]
        band.sort(key=lambda w: w[0])
        text = " ".join(w[4] for w in band).strip()

        # corta ruídos comuns que às vezes vazam
        if text:
            text = re.sub(r"\b(CID\\s?10.*|Nome do Estabelecimento.*)$", "", text).strip()
            results[field] = text

    return results

# ==== OCR de fotos (opcional e resiliente) ====================================
def ocr_bytes_if_possible(uploaded_file) -> str:
    if not ENABLE_OCR:
        return ""
    try:
        # garante diretório de modelo para evitar erro '.../.EasyOCR//model/temp.zip'
        model_dir = Path(os.getenv("EASYOCR_MODEL_STORAGE_DIRECTORY", "/home/adminuser/.EasyOCR"))
        model_dir.mkdir(parents=True, exist_ok=True)
        os.environ["EASYOCR_MODEL_STORAGE_DIRECTORY"] = str(model_dir)

        reader = easyocr.Reader(['pt', 'en'], gpu=False, download_enabled=True,
                                model_storage_directory=str(model_dir))
        img = Image.open(io.BytesIO(uploaded_file.read()))
        uploaded_file.seek(0)
        arr = np.array(img)
        result = reader.readtext(arr, detail=0, paragraph=True)
        return "\n".join(result)
    except Exception:
        st.info("OCR desativado no momento (falha ao carregar modelo). O app continua normalmente.")
        return ""

# ==== Orquestrador de extração =================================================
def extract_from_upload(uploaded_file):
    name = uploaded_file.name.lower()
    data: dict = {}
    raw_text = ""

    if name.endswith(".pdf"):
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        page = doc[0]
        raw_text = page.get_text("text") or ""
        data = extract_by_label(page)
    else:
        raw_text = ocr_bytes_if_possible(uploaded_file)  # vazio se OCR indisponível
        if raw_text:
            patterns = {
                "nome_paciente": r"Nome do Paciente\s*\n?([A-ZÀ-ÿ\s]+)",
                "nome_genitora": r"Nome da Mãe\s*\n?([A-ZÀ-ÿ\s]+)",
                "cartao_sus": r"\b(\d{15})\b",
                "data_nascimento": r"(\d{2}/\d{2}/\d{4})",
                "sexo": r"\b(Feminino|Masculino)\b",
                "raca": r"\b(BRANCA|PRETA|PARDA|AMARELA|IND[IÍ]GENA)\b",
                "prontuario": r"(?:Núm\.?\s*Prontuário|Prontuário)\s*[:\-]?\s*(\d+)",
                "municipio_referencia": r"Munic[íi]pio de Refer[êe]ncia\s*\n?([A-ZÀ-ÿ\s]+)",
                "uf": r"\b([A-Z]{2})\b",
                "cep": r"\b\d{5}-?\d{3}\b",
                "telefone_paciente": r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}",
                "endereco_completo": r"Endere[çc]o.*\n?(.+)",
            }
            for k, pat in patterns.items():
                m = re.search(pat, raw_text, re.IGNORECASE)
                if m:
                    data[k] = m.group(1).strip()

    return normalizar_campos(data), raw_text

# ==== Preenche e devolve o PDF final ==========================================
def fill_hemoba_pdf(template_path: str, data: dict) -> bytes:
    try:
        df = {k: ("" if v is None else str(v)) for k, v in data.items()}

        # radios/checkboxes do template
        for field in ["antecedente_transfusional", "antecedentes_obstetricos"]:
            sel = df.get(field, "Não")
            df[f"{field}s"] = (sel == "Sim")
            df[f"{field}n"] = (sel == "Não")

        modalidades = {
            "Programada": "modalidade_transfusaop",
            "Rotina": "modalidade_transfusaor",
            "Urgência": "modalidade_transfusaou",
            "Emergência": "modalidade_transfusaoe",
        }
        for k, f in modalidades.items():
            df[f] = (df.get("modalidade_transfusao") == k)

        for product in ["hema", "pfc", "plaquetas_prod", "crio"]:
            df[product] = bool(df.get(product))

        pdf = PdfWrapper(template_path)
        pdf.fill(df, flatten=False)
        return pdf.read()
    except Exception as e:
        st.error(f"Erro ao preencher PDF: {e}")
        raise

# ==== Estado inicial ===========================================================
if "pairs" not in st.session_state:
    st.session_state.pairs = {}
if "raw_text" not in st.session_state:
    st.session_state.raw_text = ""
if "hospital" not in st.session_state:
    st.session_state.hospital = list(HOSPITAIS.keys())[0]
if "telefone_unidade" not in st.session_state:
    st.session_state.telefone_unidade = HOSPITAIS[st.session_state.hospital]

# ==== Cabeçalho ================================================================
st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.caption(
    "Envie a **Ficha AIH (PDF)** ou **foto** (JPG/PNG). "
    "Campos com 🔵 são sugeridos a partir da AIH/OCR. Você pode revisar tudo."
)

# ==== 1) Upload ================================================================
with st.container(border=True):
    st.header("1) Enviar Ficha AIH (PDF) ou Foto")
    up = st.file_uploader("Arraste o PDF ou a foto (JPG/PNG)", type=["pdf", "jpg", "jpeg", "png"])
    if up:
        with st.spinner("Lendo documento..."):
            data, raw = extract_from_upload(up)
            st.session_state.pairs = {**st.session_state.pairs, **data}
            st.session_state.raw_text = raw
        st.success("Dados extraídos! Revise e complete abaixo.")

# helper: rótulo com bolinha se veio da AIH
def label_dot(txt: str, key: str) -> str:
    extracted = bool(st.session_state.pairs.get(key))
    cls = "label-dot" if extracted else ""
    return f"<span class='{cls}'>{txt}</span>"

# ==== 2) Formulário (sempre visível) ==========================================
with st.container(border=True):
    st.header("2) Revisar e completar formulário ↪")
    p = st.session_state.pairs  # atalho

    st.subheader("Identificação do Paciente")

    st.markdown(label_dot("Nome do Paciente", "nome_paciente"), unsafe_allow_html=True)
    p["nome_paciente"] = st.text_input("", value=p.get("nome_paciente", ""), key="nome_paciente")

    st.markdown(label_dot("Nome da Mãe", "nome_genitora"), unsafe_allow_html=True)
    p["nome_genitora"] = st.text_input("", value=p.get("nome_genitora", ""), key="nome_genitora")

    st.markdown(label_dot("Cartão SUS (CNS)", "cartao_sus"), unsafe_allow_html=True)
    p["cartao_sus"] = st.text_input("", value=p.get("cartao_sus", ""), key="cartao_sus")

    st.markdown(label_dot("Data de Nascimento (DD/MM/AAAA)", "data_nascimento"), unsafe_allow_html=True)
    p["data_nascimento"] = st.text_input("", value=p.get("data_nascimento", ""), key="data_nascimento")

    st.markdown(label_dot("Sexo", "sexo"), unsafe_allow_html=True)
    sex_opts = ["Feminino", "Masculino"]
    sex_idx = sex_opts.index(p["sexo"]) if p.get("sexo") in sex_opts else 0
    p["sexo"] = st.radio("", sex_opts, index=sex_idx, horizontal=False, key="sexo")

    st.markdown(label_dot("Raça/Cor", "raca"), unsafe_allow_html=True)
    racas = ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA"]
    r_idx = racas.index(p["raca"]) if p.get("raca") in racas else 2
    p["raca"] = st.radio("", racas, index=r_idx, horizontal=False, key="raca")

    st.markdown(label_dot("Telefone do Paciente", "telefone_paciente"), unsafe_allow_html=True)
    p["telefone_paciente"] = st.text_input("", value=p.get("telefone_paciente", ""), key="telefone_paciente")

    st.markdown(label_dot("Núm. Prontuário", "prontuario"), unsafe_allow_html=True)
    p["prontuario"] = st.text_input("", value=p.get("prontuario", ""), key="prontuario")

    st.subheader("Endereço")

    st.markdown(label_dot("Endereço completo", "endereco_completo"), unsafe_allow_html=True)
    p["endereco_completo"] = st.text_input("", value=p.get("endereco_completo", ""), key="endereco_completo")

    st.markdown(label_dot("Município de referência", "municipio_referencia"), unsafe_allow_html=True)
    p["municipio_referencia"] = st.text_input("", value=p.get("municipio_referencia", ""), key="municipio_referencia")

    st.markdown(label_dot("UF", "uf"), unsafe_allow_html=True)
    p["uf"] = st.text_input("", value=p.get("uf", ""), key="uf")

    st.markdown(label_dot("CEP", "cep"), unsafe_allow_html=True)
    p["cep"] = st.text_input("", value=p.get("cep", ""), key="cep")

    st.subheader("Estabelecimento (selecione)")

    hosp_opts = list(HOSPITAIS.keys())
    h_idx = hosp_opts.index(st.session_state.hospital)
    selected_hosp = st.radio("🏥 Hospital / Unidade de saúde", hosp_opts, index=h_idx, horizontal=False, key="hospital_radio")
    if selected_hosp != st.session_state.hospital:
        st.session_state.hospital = selected_hosp
        # atualiza telefone padrão se o campo estiver vazio
        if not st.session_state.pairs.get("telefone_unidade"):
            st.session_state.telefone_unidade = HOSPITAIS[selected_hosp]

    p["hospital"] = st.session_state.hospital
    default_phone = st.session_state.telefone_unidade or HOSPITAIS[st.session_state.hospital]
    p["telefone_unidade"] = st.text_input("📞 Telefone da Unidade (padrão, pode ajustar)", value=default_phone, key="telefone_unidade")

    st.subheader("Data e Hora")
    hoje = datetime.now()
    p["data"] = st.date_input("📅 Data", value=hoje.date(), format="YYYY/MM/DD", key="data_input")
    p["hora"] = st.time_input("⏰ Hora", value=hoje.time().replace(second=0, microsecond=0), key="hora_input")

    st.subheader("Dados clínicos (manuais)")
    p["diagnostico"] = st.text_input("🩺 Diagnóstico", value=p.get("diagnostico", ""), key="diagnostico")
    p["peso"] = st.text_input("⚖️ Peso (kg)", value=p.get("peso", ""), key="peso")
    p["antecedente_transfusional"] = st.radio("🩸 Antecedente Transfusional?", ["Não", "Sim"], index=0 if p.get("antecedente_transfusional", "Não") == "Não" else 1, key="ant_transf")
    p["antecedentes_obstetricos"] = st.radio("🤰 Antecedentes Obstétricos?", ["Não", "Sim"], index=0 if p.get("antecedentes_obstetricos", "Não") == "Não" else 1, key="ant_obs")
    p["modalidade_transfusao"] = st.radio("✍️ Modalidade de Transfusão", ["Rotina", "Programada", "Urgência", "Emergência"],
                                          index=["Rotina","Programada","Urgência","Emergência"].index(p.get("modalidade_transfusao", "Rotina")), key="modalidade")

    # Botão final (sem st.form; evita erro de "form sem submit")
    if st.button("Gerar PDF Final", type="primary"):
        final = {**p}
        final["data"] = final["data"].strftime("%d/%m/%Y") if hasattr(final["data"], "strftime") else final["data"]
        final["hora"] = final["hora"].strftime("%H:%M") if hasattr(final["hora"], "strftime") else final["hora"]
        try:
            pdf_bytes = fill_hemoba_pdf(HEMOBA_TEMPLATE_PATH, final)
            st.success("PDF gerado com sucesso!")
            st.download_button(
                "✔️ Baixar Ficha HEMOBA",
                data=pdf_bytes,
                file_name=f"HEMOBA_{(p.get('nome_paciente') or 'paciente').replace(' ', '_')}.pdf",
                mime="application/pdf",
            )
        except Exception:
            pass

# ==== Debug opcional ===========================================================
with st.expander("🐿️ Ver texto extraído e pares (debug)"):
    st.text_area("Texto bruto", st.session_state.raw_text or "", height=230)
    st.json(st.session_state.pairs or {})
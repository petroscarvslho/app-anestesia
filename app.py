import streamlit as st
import fitz  # PyMuPDF
import re
import io
import csv
import json
import unicodedata
from datetime import datetime
from PyPDFForm.wrapper import PdfWrapper

st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="wide")
st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.caption("Envie a **Ficha AIH (PDF)** para pré-preencher os campos marcados como 🔵 AIH. Você pode editar tudo antes de gerar a ficha final.")

HEMOBA_TEMPLATE_PATH = "modelo_hemo.pdf"

# =========================
# 🔧 Constantes/Config
# =========================
# ATENÇÃO: coloque os telefones corretos aqui (pegos do Google). Por padrão deixei vazio.
UNIDADES_SAUDE = {
    "Maternidade Frei Justo Venture": {"telefone": ""},  # ex: "(75) 3331-9400"
    "Hospital Regional da Chapada Diamantina": {"telefone": ""},  # ex: "(75) 3xxx-xxxx"
}

# campos que queremos tentar obter da AIH
AIH_CAMPOS = [
    "nome_paciente", "nome_genitora", "cartao_sus", "data_nascimento",
    "sexo", "raca", "endereco_completo", "municipio_referencia",
    "uf", "cep", "prontuario"
]

# =========================
# 🔩 Helpers
# =========================
def normalizar(txt: str) -> str:
    if not txt: return ""
    # remove acentos e normaliza espaços
    s = unicodedata.normalize("NFD", txt)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s

def limpar_nome(v: str) -> str:
    if not v: return ""
    v = re.sub(r"[^A-Za-zÀ-ÿ\s']", " ", v).strip()
    v = re.sub(r"\s{2,}", " ", v)
    return v

def apenas_digitos(v: str) -> str:
    return re.sub(r"\D", "", v or "")

def proxima_linha_nao_vazia(linhas, i):
    j = i + 1
    while j < len(linhas):
        t = linhas[j].strip()
        if t:
            return linhas[j].strip()
        j += 1
    return ""

# =========================
# 🧠 Extração (robusta por rótulos)
# =========================
ROTULOS = {
    # rótulo -> chave destino
    r"^nome do paciente$": "nome_paciente",
    r"^nome da mae$": "nome_genitora",
    r"^cns$|^cartao sus|^cartao do sus": "cartao_sus",
    r"^data de nasc": "data_nascimento",
    r"^sexo$": "sexo",
    r"^raca.?/?cor$|^raca$|^cor$": "raca",
    r"^endereco residencial .*|^endereco$|^endereco completo$": "endereco_completo",
    r"^municipio de referencia$|^municipio de referencia$": "municipio_referencia",
    r"^uf$": "uf",
    r"^cep$": "cep",
    r"^num\.? prontuario$|^n[uú]m\.? prontuario$|^prontuario$": "prontuario",
}

def parse_lines_by_labels(full_text: str):
    res = {}
    lines = [l.rstrip() for l in full_text.splitlines()]
    # mapa normalizado->original para capturar corretamente mesmo com acento/deslocamento
    norm_lines = [normalizar(l).lower() for l in lines]

    # 1) varre rótulos conhecidos (valor = próxima linha não vazia)
    for i, n in enumerate(norm_lines):
        for pat, chave in ROTULOS.items():
            if re.search(pat, n):
                val = proxima_linha_nao_vazia(lines, i)  # usa a linha original como valor
                if chave in ("nome_paciente", "nome_genitora"):
                    val = limpar_nome(val)
                if chave == "cartao_sus":
                    d = apenas_digitos(val)
                    if len(d) >= 11:
                        val = d
                if chave == "cep":
                    m = re.search(r"\b\d{2}\.?\d{3}-?\d{3}\b|\b\d{5}-\d{3}\b", val)
                    if m: val = m.group(0)
                if chave == "uf":
                    m = re.search(r"\b[A-Z]{2}\b", val)
                    if m: val = m.group(0)
                res.setdefault(chave, val)

    # 2) heurísticas extras caso algo falhe
    if "sexo" not in res:
        for n, raw in zip(norm_lines, lines):
            if "feminino" in n: res["sexo"] = "Feminino"; break
            if "masculino" in n: res["sexo"] = "Masculino"; break

    if "raca" not in res:
        for n, raw in zip(norm_lines, lines):
            if "parda" in n: res["raca"] = "Parda"; break
            if "branca" in n: res["raca"] = "Branca"; break
            if "preta" in n: res["raca"] = "Preta"; break
            if "amarela" in n: res["raca"] = "Amarela"; break
            if "indigena" in n or "indígena" in raw.lower(): res["raca"] = "Indígena"; break

    # 3) ajuste de endereço: se vier CEP/UF/município separados, monta "endereco_completo" amigável
    if "endereco_completo" not in res:
        base = []
        # tenta achar algo que pareça endereço solto (ex: "POV GUARIBAS, SN, ZONA RURAL")
        for raw in lines:
            if re.search(r"\bSN\b|\bRUA\b|\bAV\b|ZONA|BAIRRO|POV|TRAV|ALAMEDA|COND\.", raw, re.IGNORECASE):
                base.append(raw.strip())
        if base:
            res["endereco_completo"] = base[0]

    return res

def extract_from_pdf(fileobj):
    try:
        doc = fitz.open(stream=fileobj.read(), filetype="pdf")
        txt = ""
        for p in doc:
            # usar "text" simples evita quebra de blocos estranhos
            txt += p.get_text("text") + "\n"
        parsed = parse_lines_by_labels(txt)
        return txt, parsed
    except Exception as e:
        st.error(f"Erro ao ler o PDF: {e}")
        return "", {}

# =========================
# 🧾 Log em memória + download
# =========================
def append_log(entry: dict):
    if "extraction_log" not in st.session_state:
        st.session_state.extraction_log = []
    st.session_state.extraction_log.append(entry)

def download_buttons_for_log():
    if "extraction_log" not in st.session_state or not st.session_state.extraction_log:
        return
    st.markdown("**Baixar LOG das extrações**")
    # JSONL
    buf_jsonl = io.StringIO()
    for row in st.session_state.extraction_log:
        buf_jsonl.write(json.dumps(row, ensure_ascii=False) + "\n")
    st.download_button("⬇️ Baixar .jsonl", buf_jsonl.getvalue().encode("utf-8"), "extracoes.jsonl", "application/json")
    # CSV
    buf_csv = io.StringIO()
    writer = csv.DictWriter(buf_csv, fieldnames=sorted({k for r in st.session_state.extraction_log for k in r.keys()}))
    writer.writeheader()
    for r in st.session_state.extraction_log:
        writer.writerow(r)
    st.download_button("⬇️ Baixar .csv", buf_csv.getvalue().encode("utf-8"), "extracoes.csv", "text/csv")

# =========================
# 🧱 Estado inicial do formulário
# =========================
if "form_values" not in st.session_state:
    st.session_state.form_values = {k: "" for k in [
        # Identificação
        "nome_paciente", "nome_genitora", "cartao_sus", "data_nascimento", "sexo", "raca",
        # Contato e prontuário
        "telefone_paciente", "prontuario",
        # Endereço
        "endereco_completo", "municipio_referencia", "uf", "cep",
        # Estabelecimento
        "hospital", "unidade_saude", "telefone_unidade",
        # Data/Hora
        "data", "hora",
        # Campos clínicos (manuais, exemplo)
        "diagnostico", "peso", "antecedente_transfusional", "antecedentes_obstetricos",
        "modalidade_transfusao"
    ]}
    # defaults úteis
    st.session_state.form_values["data"] = datetime.now().strftime("%d/%m/%Y")
    st.session_state.form_values["hora"] = datetime.now().strftime("%H:%M")
    st.session_state.form_values["modalidade_transfusao"] = "Rotina"
    st.session_state.form_values["antecedente_transfusional"] = "Não"
    st.session_state.form_values["antecedentes_obstetricos"] = "Não"
    st.session_state.aih_filled = {k: False for k in AIH_CAMPOS}

# =========================
# 1) Upload (auto-extração)
# =========================
with st.container(border=True):
    st.header("1) Enviar Ficha AIH (PDF)")
    up = st.file_uploader("Arraste o PDF aqui", type="pdf", label_visibility="collapsed")
    if up is not None and st.session_state.get("last_uploaded") != up.name:
        with st.spinner("Lendo e extraindo..."):
            raw_text, parsed = extract_from_pdf(up)
            # guarda debug
            st.session_state.last_raw_text = raw_text
            # aplica nos campos e marca origem
            for k in AIH_CAMPOS:
                if k in parsed and parsed[k]:
                    st.session_state.form_values[k] = str(parsed[k]).strip()
                    st.session_state.aih_filled[k] = True
            # data/hora atuais (se quiser usar da AIH, ajuste aqui)
            st.session_state.form_values["data"] = datetime.now().strftime("%d/%m/%Y")
            st.session_state.form_values["hora"] = datetime.now().strftime("%H:%M")

            # log
            append_log({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "arquivo": up.name,
                **{k: st.session_state.form_values.get(k, "") for k in AIH_CAMPOS}
            })

            st.session_state.last_uploaded = up.name
            st.success("Dados extraídos! Revise e complete abaixo.")

# =========================
# 2) Revisar e completar formulário
# =========================
st.header("2) Revisar e completar formulário ↪")

def label_aih(txt, key):
    return f"{'🔵' if st.session_state.aih_filled.get(key) else '⚪'} {txt}"

# Seção: Identificação do Paciente
with st.form("form_hemoba"):
    st.subheader("Identificação do Paciente")
    c1, c2 = st.columns(2)
    with c1:
        st.session_state.form_values["nome_paciente"] = st.text_input(
            label_aih("Nome do Paciente", "nome_paciente"),
            value=st.session_state.form_values["nome_paciente"]
        )
        st.session_state.form_values["cartao_sus"] = st.text_input(
            label_aih("Cartão SUS (CNS)", "cartao_sus"),
            value=st.session_state.form_values["cartao_sus"]
        )
        st.session_state.form_values["sexo"] = st.text_input(
            label_aih("Sexo", "sexo"),
            value=st.session_state.form_values["sexo"]
        )
    with c2:
        st.session_state.form_values["nome_genitora"] = st.text_input(
            label_aih("Nome da Mãe", "nome_genitora"),
            value=st.session_state.form_values["nome_genitora"]
        )
        st.session_state.form_values["data_nascimento"] = st.text_input(
            label_aih("Data de Nascimento (DD/MM/AAAA)", "data_nascimento"),
            value=st.session_state.form_values["data_nascimento"]
        )
        st.session_state.form_values["raca"] = st.text_input(
            label_aih("Raça/Cor", "raca"),
            value=st.session_state.form_values["raca"]
        )

    # Contato / Prontuário
    c3, c4 = st.columns(2)
    with c3:
        st.session_state.form_values["telefone_paciente"] = st.text_input(
            "⚪ Telefone do Paciente",
            value=st.session_state.form_values["telefone_paciente"]
        )
    with c4:
        st.session_state.form_values["prontuario"] = st.text_input(
            label_aih("Prontuário", "prontuario"),
            value=st.session_state.form_values["prontuario"]
        )

    # Endereço
    st.subheader("Endereço")
    c5, c6, c7 = st.columns([3,1,1])
    with c5:
        st.session_state.form_values["endereco_completo"] = st.text_input(
            label_aih("Endereço completo", "endereco_completo"),
            value=st.session_state.form_values["endereco_completo"]
        )
    with c6:
        st.session_state.form_values["uf"] = st.text_input(
            label_aih("UF", "uf"),
            value=st.session_state.form_values["uf"]
        )
    with c7:
        st.session_state.form_values["cep"] = st.text_input(
            label_aih("CEP", "cep"),
            value=st.session_state.form_values["cep"]
        )
    st.session_state.form_values["municipio_referencia"] = st.text_input(
        label_aih("Município de referência", "municipio_referencia"),
        value=st.session_state.form_values["municipio_referencia"]
    )

    # Estabelecimento (seleção manual; sem callback dentro do form)
    st.subheader("Estabelecimento (selecione)")
    # escolha do hospital/unidade
    hosp_opcoes = list(UNIDADES_SAUDE.keys())
    # Valor inicial
    if st.session_state.form_values["hospital"] == "":
        st.session_state.form_values["hospital"] = hosp_opcoes[0]
    st.session_state.form_values["hospital"] = st.selectbox(
        "🏥 Hospital / Unidade de saúde",
        options=hosp_opcoes,
        index=hosp_opcoes.index(st.session_state.form_values["hospital"])
    )
    # telefone da unidade = do dicionário, mas editável
    tel_padrao = UNIDADES_SAUDE.get(st.session_state.form_values["hospital"], {}).get("telefone", "")
    if st.session_state.form_values["telefone_unidade"] == "" and tel_padrao:
        st.session_state.form_values["telefone_unidade"] = tel_padrao

    st.session_state.form_values["telefone_unidade"] = st.text_input(
        "⚪ Telefone da Unidade (padrão, pode ajustar)",
        value=st.session_state.form_values["telefone_unidade"]
    )

    # Data/Hora (auto)
    c8, c9 = st.columns(2)
    with c8:
        st.session_state.form_values["data"] = st.text_input(
            "⚪ Data",
            value=st.session_state.form_values["data"]
        )
    with c9:
        st.session_state.form_values["hora"] = st.text_input(
            "⚪ Hora",
            value=st.session_state.form_values["hora"]
        )

    # Campos clínicos (exemplos manuais)
    st.subheader("Dados clínicos (manuais)")
    c10, c11 = st.columns(2)
    with c10:
        st.session_state.form_values["diagnostico"] = st.text_input("⚪ Diagnóstico", value=st.session_state.form_values["diagnostico"])
        st.session_state.form_values["antecedente_transfusional"] = st.selectbox("⚪ Antecedente Transfusional?", ["Não", "Sim"], index=0 if st.session_state.form_values["antecedente_transfusional"]!="Sim" else 1)
    with c11:
        st.session_state.form_values["peso"] = st.text_input("⚪ Peso (kg)", value=st.session_state.form_values["peso"])
        st.session_state.form_values["antecedentes_obstetricos"] = st.selectbox("⚪ Antecedentes Obstétricos?", ["Não", "Sim"], index=0 if st.session_state.form_values["antecedentes_obstetricos"]!="Sim" else 1)

    st.session_state.form_values["modalidade_transfusao"] = st.selectbox(
        "⚪ Modalidade de Transfusão", ["Rotina", "Programada", "Urgência", "Emergência"],
        index=["Rotina","Programada","Urgência","Emergência"].index(st.session_state.form_values["modalidade_transfusao"])
    )

    submitted = st.form_submit_button("Gerar PDF Final", type="primary")
    if submitted:
        try:
            pdf = PdfWrapper(HEMOBA_TEMPLATE_PATH)
            payload = {k: ("" if v is None else str(v)) for k, v in st.session_state.form_values.items()}

            # Exemplo de mapeamento de radios/checkboxes se existir no seu PDF:
            # (ajuste os nomes dos campos do seu template)
            for field in ['antecedente_transfusional', 'antecedentes_obstetricos']:
                sel = st.session_state.form_values.get(field, "Não")
                payload[f'{field}s'] = (sel == 'Sim')
                payload[f'{field}n'] = (sel == 'Não')

            modalidades = {
                "Programada": "modalidade_transfusaop",
                "Rotina": "modalidade_transfusaor",
                "Urgência": "modalidade_transfusaou",
                "Emergência": "modalidade_transfusaoe"
            }
            sel_mod = st.session_state.form_values.get("modalidade_transfusao", "Rotina")
            for k, fld in modalidades.items():
                payload[fld] = (k == sel_mod)

            pdf.fill(payload, flatten=False)
            data_bytes = pdf.read()

            st.success("PDF gerado com sucesso!")
            st.download_button(
                "⬇️ Baixar Ficha HEMOBA",
                data=data_bytes,
                file_name=f"HEMOBA_{st.session_state.form_values.get('nome_paciente','paciente').replace(' ','_')}.pdf",
                mime="application/pdf"
            )
        except Exception as e:
            st.error(f"Erro ao preencher PDF: {e}")

# =========================
# 🧪 Debug / Logs
# =========================
with st.expander("🪵 Ver texto extraído e pares (debug)"):
    colA, colB = st.columns(2)
    with colA:
        st.markdown("**Texto bruto**")
        st.text_area("raw", st.session_state.get("last_raw_text", ""), height=260, label_visibility="collapsed")
    with colB:
        st.markdown("**Pares chave→valor parseados (AIH)**")
        parsed_now = {k: st.session_state.form_values.get(k, "") for k in AIH_CAMPOS}
        st.json(parsed_now)

download_buttons_for_log()

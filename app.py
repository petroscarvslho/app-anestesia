import streamlit as st
import fitz  # PyMuPDF
import re, json
from datetime import datetime
from PyPDFForm.wrapper import PdfWrapper
import io

# ------------------ CONFIG ------------------
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="wide")
st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.markdown("Envie a **Ficha AIH (PDF)** para pré-preencher os campos marcados como 🟢 AIH. \
Você pode editar tudo antes de gerar a ficha final.")

HEMOBA_TEMPLATE_PATH = "modelo_hemo.pdf"

# Telefones padrão por unidade/hospital (ajuste se quiser)
HOSPITAIS = {
    "Maternidade Frei Justo Venture": {"telefone_unidade": "75 3331-9400"},
    "Hospital Regional da Chapada Diamantina": {"telefone_unidade": ""},  # atualize aqui se quiser padrão
}

# Opções fixas
OPCOES_SEXO = ["Feminino", "Masculino"]
OPCOES_RACA = ["Branca", "Parda", "Preta", "Amarela", "Indígena", "Não informada"]
OPCOES_HOSPITAL = list(HOSPITAIS.keys())
OPCOES_MODALIDADE = ["Rotina", "Programada", "Urgência", "Emergência"]

# ------------------ ESTADO ------------------
def ensure_state():
    if "form_values" not in st.session_state:
        st.session_state.form_values = {
            # Identificação
            "nome_paciente": "",
            "nome_genitora": "",
            "cartao_sus": "",
            "data_nascimento": "",
            "sexo": "",
            "raca_cor": "",
            "telefone_paciente": "",
            "identidade": "",
            "cpf": "",
            "prontuario": "",

            # Endereço
            "endereco_completo": "",
            "municipio_origem": "",
            "uf": "",
            "cep": "",

            # Estabelecimento (manuais)
            "hospital": OPCOES_HOSPITAL[0],
            "unidade_saude": OPCOES_HOSPITAL[0],
            "telefone_unidade": HOSPITAIS[OPCOES_HOSPITAL[0]]["telefone_unidade"],

            # Emissão
            "data": datetime.now().strftime("%d/%m/%Y"),
            "hora": datetime.now().strftime("%H:%M"),

            # Clínico / transfusional
            "diagnostico": "",
            "cid10_principal": "",
            "cid10_secundario": "",
            "peso": "",
            "antecedente_transfusional": "Não",
            "antecedentes_obstetricos": "Não",
            "modalidade_transfusao": "Rotina",

            # Produtos e quantidades
            "hema": False, "qtd_hema": 0,
            "pfc": False, "qtd_pfc": 0,
            "plaquetas_prod": False, "qtd_plaquetas": 0,
            "crio": False, "qtd_crio": 0,
        }
    if "autofilled" not in st.session_state:
        st.session_state.autofilled = {k: False for k in st.session_state.form_values}
    if "raw_text" not in st.session_state:
        st.session_state.raw_text = ""
    if "parsed" not in st.session_state:
        st.session_state.parsed = {}
    if "last_uploaded_name" not in st.session_state:
        st.session_state.last_uploaded_name = None

ensure_state()

# ------------------ EXTRAÇÃO ------------------
def norm(s):
    return (s or "").strip()

def try_pick(options, value):
    v = (value or "").strip().lower()
    for opt in options:
        if opt.lower().startswith(v) or v.startswith(opt.lower()):
            return opt
    return value or ""

def extract_data_pass_A(full_text):
    t = full_text

    # padrões mais abrangentes (labels variam e pulam linha)
    patterns = {
        "nome_paciente": r"Nome\s+do\s+Paciente\s*\n(.+)",
        "nome_genitora": r"Nome\s+da\s+M[ãa]e\s*\n(.+)",

        "cartao_sus": r"\bCNS\b\s*\n([\d\s\.]{11,})",
        "data_nascimento": r"Data\s+de\s+Nasc\s*\n(\d{2}/\d{2}/\d{4})",
        "sexo": r"\bSexo\b\s*\n([A-Za-zÀ-ÿ]+)",
        "raca_cor": r"Ra[çc]a/?cor\s*\n([A-ZÀ-ÿ]+)",

        "telefone_paciente": r"Telefone\s+Celular\s*\n([\(\)\d\-\s]+)",
        "identidade": r"\bIdentidade\b\s*\n([\w\.\-\/]+)",
        "cpf": r"\bCPF\b\s*\n([\d\.\-]+)",
        "prontuario": r"N[úu]m\.?\s*Prontu[áa]rio\s*\n(\d+)",

        "endereco_completo": r"Endere[çc]o\s+Residencial.*?\n(.+)",
        "municipio_origem": r"Munic[ií]pio\s+de\s+Refer[êe]ncia\s*\n([A-ZÀ-ÿ\s\-]+)",
        "uf": r"\bUF\b\s*\n([A-Z]{2})",
        "cep": r"\bCEP\b\s*\n([\d\.\-]+)",

        "cid10_principal": r"CID\s*[-\s]*10\s*Principal\s*\n([A-Z]\d{2}(?:\.\d{1,2})?)",
        "cid10_secundario": r"Diagn[óo]stico\s+Secund[áa]rio\s*\n([A-Z]\d{2}(?:\.\d{1,2})?)",

        # Infos do estabelecimento (apenas para log)
        "estab_solicitante": r"Nome\s+do\s+Estabelecimento\s+Solicitante\s*\n(.+)",
        "estab_executante": r"Nome\s+do\s+Estabelecimento\s+Executante\s*\n(.+)",
        "cnes": r"\bCNES\b\s*\n(\d+)",
        "cnpj": r"CNPJ:?\s*\n?([\d\./\-]+)",
        "telefone_unidade_aih": r"Tel\s*[-:]?\s*([\d\s\-\(\)]+)",
    }

    results = {}
    for key, pat in patterns.items():
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            results[key] = norm(m.group(1))

    # Limpezas pontuais
    if "cartao_sus" in results:
        results["cartao_sus"] = re.sub(r"\D", "", results["cartao_sus"])
    if "telefone_paciente" in results:
        tel = re.sub(r"[^\d]", "", results["telefone_paciente"])
        if len(tel) >= 10:
            results["telefone_paciente"] = tel
        else:
            results.pop("telefone_paciente", None)
    if "cpf" in results:
        results["cpf"] = re.sub(r"[^\d]", "", results["cpf"])
    if "cep" in results:
        results["cep"] = re.sub(r"[^\d]", "", results["cep"])

    # Normalizações de escolhas
    if "sexo" in results:
        results["sexo"] = try_pick(OPCOES_SEXO, results["sexo"])
    if "raca_cor" in results:
        # padroniza para opções conhecidas se possível
        m = results["raca_cor"].capitalize()
        results["raca_cor"] = try_pick(OPCOES_RACA, m)

    return results

def extract_data_pass_B(page):
    """Resgate por posição (ajuste as faixas se necessário para seu layout)."""
    results = {}
    blocks = page.get_text("blocks")
    blocks.sort(key=lambda b: (b[1], b[0]))  # y, x

    bands = {
        "nome_paciente": (580, 630),
        "nome_genitora": (500, 545),
        "cartao_sus": (665, 710),
        "prontuario": (725, 770),
    }

    for key, (y0, y1) in bands.items():
        for b in blocks:
            _, y_top, _, y_bot, text, *_ = b
            y_center = (y_top + y_bot) / 2
            if y0 <= y_center <= y1:
                text = norm(text)
                if key in ("cartao_sus", "prontuario"):
                    digits = re.sub(r"\D", "", text)
                    if digits:
                        results[key] = digits
                        break
                else:
                    if len(text) > 1:
                        results[key] = text
                        break
    return results

def extract_from_pdf(uploaded_file):
    raw_text = ""
    parsed = {}
    autofilled_keys = []

    try:
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        if not doc:
            return raw_text, parsed, autofilled_keys

        # Pego o texto da primeira página (ajuste se quiser concatenar todas)
        page0 = doc[0]
        raw_text = page0.get_text("text")

        # Passagem A
        a = extract_data_pass_A(raw_text)

        # Se faltar algo crítico, tenta resgatar via posição
        critical = ["nome_paciente", "nome_genitora", "cartao_sus", "prontuario"]
        missing = [f for f in critical if not a.get(f)]
        if missing:
            b = extract_data_pass_B(page0)
            for k in missing:
                if b.get(k):
                    a[k] = b[k]

        parsed = a

        # Calcula quais chaves serão marcadas como autofill depois
        autofilled_keys = list(parsed.keys())
        return raw_text, parsed, autofilled_keys
    except Exception as e:
        st.error(f"Erro ao ler a AIH: {e}")
        return raw_text, parsed, autofilled_keys

# ------------------ UI HELPERS ------------------
def label_with_status(label, key):
    return f"{'🟢' if st.session_state.autofilled.get(key) else '⚪'} {label}"

def apply_autofill(parsed_dict):
    """Preenche st.session_state.form_values com valores extraídos e marca autofilled."""
    for k, v in parsed_dict.items():
        if k in st.session_state.form_values and v:
            st.session_state.form_values[k] = v
            st.session_state.autofilled[k] = True

def update_unidade_phone_if_needed():
    sel = st.session_state.form_values.get("unidade_saude")
    padrao = HOSPITAIS.get(sel, {}).get("telefone_unidade", "")
    if not st.session_state.form_values.get("telefone_unidade"):
        st.session_state.form_values["telefone_unidade"] = padrao

# ------------------ UPLOAD + EXTRAÇÃO ------------------
with st.container(border=True):
    st.subheader("1) Enviar Ficha AIH (PDF)")
    uploaded = st.file_uploader("Selecione o arquivo PDF da AIH", type="pdf", label_visibility="collapsed")

    if uploaded is not None:
        if st.session_state.last_uploaded_name != uploaded.name:
            with st.spinner("Lendo e extraindo dados da AIH..."):
                raw, parsed, auto_keys = extract_from_pdf(uploaded)
                st.session_state.raw_text = raw
                st.session_state.parsed = parsed
                # aplica no formulário e marca campos
                apply_autofill(parsed)
                st.session_state.last_uploaded_name = uploaded.name
                # exibe feedback rápido
                st.success(f"Extração concluída. Campos AIH preenchidos: {', '.join(sorted(k for k in auto_keys if k in st.session_state.form_values)) or '-'}")

# ------------------ FORMULÁRIO (sempre visível) ------------------
st.subheader("2) Revisar e completar formulário")

with st.form("form_hemoba", clear_on_submit=False):
    # --- Identificação ---
    st.markdown("### Identificação do Paciente")
    c1, c2 = st.columns(2)
    with c1:
        st.session_state.form_values["nome_paciente"] = st.text_input(
            label_with_status("Nome do Paciente", "nome_paciente"),
            value=st.session_state.form_values["nome_paciente"],
            key="fv_nome_paciente"
        )
        st.session_state.form_values["cartao_sus"] = st.text_input(
            label_with_status("Cartão SUS (CNS)", "cartao_sus"),
            value=st.session_state.form_values["cartao_sus"],
            key="fv_cartao_sus"
        )
        st.session_state.form_values["sexo"] = st.selectbox(
            label_with_status("Sexo", "sexo"),
            OPCOES_SEXO,
            index=(OPCOES_SEXO.index(st.session_state.form_values["sexo"]) if st.session_state.form_values["sexo"] in OPCOES_SEXO else 0),
            key="fv_sexo"
        )
        st.session_state.form_values["telefone_paciente"] = st.text_input(
            label_with_status("Telefone do Paciente", "telefone_paciente"),
            value=st.session_state.form_values["telefone_paciente"],
            key="fv_telefone_paciente"
        )
    with c2:
        st.session_state.form_values["nome_genitora"] = st.text_input(
            label_with_status("Nome da Mãe", "nome_genitora"),
            value=st.session_state.form_values["nome_genitora"],
            key="fv_nome_genitora"
        )
        st.session_state.form_values["data_nascimento"] = st.text_input(
            label_with_status("Data de Nascimento (DD/MM/AAAA)", "data_nascimento"),
            value=st.session_state.form_values["data_nascimento"],
            key="fv_data_nascimento"
        )
        st.session_state.form_values["raca_cor"] = st.selectbox(
            label_with_status("Raça/Cor", "raca_cor"),
            OPCOES_RACA,
            index=(OPCOES_RACA.index(st.session_state.form_values["raca_cor"]) if st.session_state.form_values["raca_cor"] in OPCOES_RACA else OPCOES_RACA.index("Não informada")),
            key="fv_raca_cor"
        )
        st.session_state.form_values["prontuario"] = st.text_input(
            label_with_status("Prontuário", "prontuario"),
            value=st.session_state.form_values["prontuario"],
            key="fv_prontuario"
        )

    # --- Endereço ---
    st.markdown("### Endereço")
    st.session_state.form_values["endereco_completo"] = st.text_input(
        label_with_status("Endereço completo", "endereco_completo"),
        value=st.session_state.form_values["endereco_completo"],
        key="fv_endereco_completo"
    )
    c3, c4, c5 = st.columns([1.2, 0.5, 0.8])
    with c3:
        st.session_state.form_values["municipio_origem"] = st.text_input(
            label_with_status("Município de referência", "municipio_origem"),
            value=st.session_state.form_values["municipio_origem"],
            key="fv_municipio_origem"
        )
    with c4:
        st.session_state.form_values["uf"] = st.text_input(
            label_with_status("UF", "uf"),
            value=st.session_state.form_values["uf"],
            key="fv_uf"
        )
    with c5:
        st.session_state.form_values["cep"] = st.text_input(
            label_with_status("CEP", "cep"),
            value=st.session_state.form_values["cep"],
            key="fv_cep"
        )

    # --- Estabelecimento ---
    st.markdown("### Estabelecimento (selecione)")
    c6, c7 = st.columns(2)
    with c6:
        st.session_state.form_values["hospital"] = st.selectbox(
            "⚪ Hospital",
            OPCOES_HOSPITAL,
            index=OPCOES_HOSPITAL.index(st.session_state.form_values["hospital"]) if st.session_state.form_values["hospital"] in OPCOES_HOSPITAL else 0,
            key="fv_hospital"
        )
    with c7:
        st.session_state.form_values["unidade_saude"] = st.selectbox(
            "⚪ Unidade de saúde",
            OPCOES_HOSPITAL,
            index=OPCOES_HOSPITAL.index(st.session_state.form_values["unidade_saude"]) if st.session_state.form_values["unidade_saude"] in OPCOES_HOSPITAL else 0,
            key="fv_unidade_saude",
            on_change=update_unidade_phone_if_needed
        )
    st.session_state.form_values["telefone_unidade"] = st.text_input(
        "⚪ Telefone da unidade (padrão pela seleção, pode editar)",
        value=st.session_state.form_values["telefone_unidade"],
        key="fv_telefone_unidade"
    )

    # --- Emissão ---
    st.markdown("### Emissão")
    c8, c9 = st.columns(2)
    with c8:
        st.session_state.form_values["data"] = st.text_input(
            label_with_status("Data (emissão)", "data"),
            value=st.session_state.form_values["data"],
            key="fv_data"
        )
    with c9:
        st.session_state.form_values["hora"] = st.text_input(
            label_with_status("Hora (emissão)", "hora"),
            value=st.session_state.form_values["hora"],
            key="fv_hora"
        )

    # --- Clínico / transfusional ---
    st.markdown("### Dados clínicos e transfusionais")
    st.session_state.form_values["diagnostico"] = st.text_input(
        label_with_status("Diagnóstico", "diagnostico"),
        value=st.session_state.form_values["diagnostico"],
        key="fv_diagnostico"
    )
    c10, c11, c12 = st.columns(3)
    with c10:
        st.session_state.form_values["cid10_principal"] = st.text_input(
            label_with_status("CID-10 principal", "cid10_principal"),
            value=st.session_state.form_values["cid10_principal"],
            key="fv_cid10_principal"
        )
    with c11:
        st.session_state.form_values["cid10_secundario"] = st.text_input(
            label_with_status("CID-10 secundário", "cid10_secundario"),
            value=st.session_state.form_values["cid10_secundario"],
            key="fv_cid10_secundario"
        )
    with c12:
        st.session_state.form_values["peso"] = st.text_input(
            label_with_status("Peso (kg)", "peso"),
            value=st.session_state.form_values["peso"],
            key="fv_peso"
        )

    c13, c14, c15 = st.columns(3)
    with c13:
        st.session_state.form_values["antecedente_transfusional"] = st.selectbox(
            label_with_status("Antecedente transfusional?", "antecedente_transfusional"),
            ["Não", "Sim"],
            index=0 if st.session_state.form_values["antecedente_transfusional"] != "Sim" else 1,
            key="fv_antecedente_transfusional"
        )
    with c14:
        st.session_state.form_values["antecedentes_obstetricos"] = st.selectbox(
            label_with_status("Antecedentes obstétricos?", "antecedentes_obstetricos"),
            ["Não", "Sim"],
            index=0 if st.session_state.form_values["antecedentes_obstetricos"] != "Sim" else 1,
            key="fv_antecedentes_obstetricos"
        )
    with c15:
        st.session_state.form_values["modalidade_transfusao"] = st.selectbox(
            label_with_status("Modalidade de transfusão", "modalidade_transfusao"),
            OPCOES_MODALIDADE,
            index=OPCOES_MODALIDADE.index(st.session_state.form_values["modalidade_transfusao"]) if st.session_state.form_values["modalidade_transfusao"] in OPCOES_MODALIDADE else 0,
            key="fv_modalidade_transfusao"
        )

    st.markdown("### Produtos e quantidades")
    c16, c17 = st.columns(2)
    with c16:
        st.session_state.form_values["hema"] = st.checkbox(
            "Hemácias", value=st.session_state.form_values["hema"], key="fv_hema"
        )
        st.session_state.form_values["qtd_hema"] = st.number_input(
            "Quantidade de Hemácias", min_value=0, step=1, value=int(st.session_state.form_values["qtd_hema"]), key="fv_qtd_hema"
        )
        st.session_state.form_values["pfc"] = st.checkbox(
            "PFC", value=st.session_state.form_values["pfc"], key="fv_pfc"
        )
        st.session_state.form_values["qtd_pfc"] = st.number_input(
            "Quantidade de PFC", min_value=0, step=1, value=int(st.session_state.form_values["qtd_pfc"]), key="fv_qtd_pfc"
        )
    with c17:
        st.session_state.form_values["plaquetas_prod"] = st.checkbox(
            "Plaquetas", value=st.session_state.form_values["plaquetas_prod"], key="fv_plaquetas"
        )
        st.session_state.form_values["qtd_plaquetas"] = st.number_input(
            "Quantidade de Plaquetas", min_value=0, step=1, value=int(st.session_state.form_values["qtd_plaquetas"]), key="fv_qtd_plaquetas"
        )
        st.session_state.form_values["crio"] = st.checkbox(
            "Crioprecipitado", value=st.session_state.form_values["crio"], key="fv_crio"
        )
        st.session_state.form_values["qtd_crio"] = st.number_input(
            "Quantidade de Crioprecipitado", min_value=0, step=1, value=int(st.session_state.form_values["qtd_crio"]), key="fv_qtd_crio"
        )

    # --- Ações ---
    cA, cB = st.columns([0.5, 0.5])
    with cA:
        gerar = st.form_submit_button("✔️ Gerar PDF Final", type="primary")
    with cB:
        # só para forçar um "reset" visual do selo AIH se você quiser
        limpar_aih = st.form_submit_button("Limpar selos AIH (manter valores)")

    if limpar_aih:
        st.session_state.autofilled = {k: False for k in st.session_state.form_values}

    if gerar:
        # monta payload para o PDF
        data = dict(st.session_state.form_values)
        try:
            pdf_form = PdfWrapper(HEMOBA_TEMPLATE_PATH)
            # Mapas de booleanos e modalidade (se o seu PDF usar nomes diferentes, ajuste aqui)
            for field in ["antecedente_transfusional", "antecedentes_obstetricos"]:
                sel = data.get(field, "Não")
                data[f"{field}s"] = (sel == "Sim")
                data[f"{field}n"] = (sel != "Sim")

            modalidades = {
                "Programada": "modalidade_transfusaop",
                "Rotina": "modalidade_transfusaor",
                "Urgência": "modalidade_transfusaou",
                "Emergência": "modalidade_transfusaoe",
            }
            selected = data.get("modalidade_transfusao")
            for nome, campo in modalidades.items():
                data[campo] = (nome == selected)

            # checkboxes de produtos já estão em data; quantidades também
            pdf_form.fill({k: str(v) for k, v in data.items()}, flatten=False)
            out = pdf_form.read()

            st.success("PDF gerado com sucesso!")
            st.download_button(
                "Baixar Ficha HEMOBA",
                data=out,
                file_name=f"HEMOBA_{(data.get('nome_paciente') or 'paciente').replace(' ', '_')}.pdf",
                mime="application/pdf",
            )
        except Exception as e:
            st.error(f"Erro ao preencher o PDF: {e}")

# ------------------ LOGS ------------------
with st.expander("📜 Logs da extração (texto bruto e JSON)"):
    st.caption("Texto bruto extraído da AIH (primeira página):")
    st.text(st.session_state.raw_text or "(vazio)")
    st.caption("Campos parseados:")
    st.json(st.session_state.parsed or {})

    # Downloads
    raw_bytes = (st.session_state.raw_text or "").encode("utf-8")
    json_bytes = json.dumps(st.session_state.parsed or {}, ensure_ascii=False, indent=2).encode("utf-8")

    cdl1, cdl2 = st.columns(2)
    with cdl1:
        st.download_button("⬇️ Baixar log_extracao.txt", data=raw_bytes, file_name="log_extracao.txt", mime="text/plain")
    with cdl2:
        st.download_button("⬇️ Baixar extracao.json", data=json_bytes, file_name="extracao.json", mime="application/json")

# app.py — Consultor de Embalagens (Streamlit) + frete SuperFrete opcional
import os
import re
import json
import time
import requests
import streamlit as st
from json import JSONDecodeError
from openai import OpenAI

# ==================== CONFIG BÁSICA ====================
st.set_page_config(
    page_title="Consultor de Embalagens • SuperFrete",
    page_icon="📦",
    layout="centered"
)

# ==================== ESTILO (Poppins + #0fae79) ====================
st.markdown(
    "<link href='https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap' rel='stylesheet'>",
    unsafe_allow_html=True
)
st.markdown("""
<style>
:root { --sf-accent:#0fae79; }
html, body, [class*="css"], [data-testid="stAppViewContainer"] * {
  font-family:'Poppins',sans-serif !important;
}
h1,h2,h3,h4,h5,h6 { color:var(--sf-accent)!important; font-weight:700!important; }
.stButton > button {
  background:var(--sf-accent)!important; color:#fff!important; border:none!important;
  border-radius:10px!important; padding:.65rem 1.2rem!important; font-weight:600!important;
}
.stButton > button:hover { filter:brightness(.92); }
a { color:var(--sf-accent)!important; text-decoration:none; }
a:hover { text-decoration:underline; }
[data-baseweb="input"] input,[data-baseweb="textarea"] textarea{
  border:1px solid var(--sf-accent)!important; border-radius:8px!important;
}
.block-container{ padding-top:2rem!important; }
</style>
""", unsafe_allow_html=True)

# ==================== ENV VARS ====================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # obrigatório
SUPERFRETE_API_TOKEN = os.getenv("SUPERFRETE_API_TOKEN")  # obrigatório p/ frete
SUPERFRETE_CONTACT_EMAIL = os.getenv("SUPERFRETE_CONTACT_EMAIL", "contato@superfrete.com")
SUPERFRETE_USE_SANDBOX = os.getenv("SUPERFRETE_USE_SANDBOX", "false").lower() == "true"
SUPERFRETE_SERVICES = os.getenv("SUPERFRETE_SERVICES", "1,2,17")  # ex.: PAC, SEDEX, Mini Envios

# ==================== HELPERS ====================
def parse_dimensions(dim_str: str):
    """'20x15x10' -> (20.0,15.0,10.0) em cm."""
    if not dim_str:
        return None
    m = re.search(
        r'(\d+(?:[.,]\d+)?)\s*[xX]\s*(\d+(?:[.,]\d+)?)\s*[xX]\s*(\d+(?:[.,]\d+)?)',
        dim_str
    )
    if not m:
        return None
    c, l, a = m.groups()
    to_f = lambda s: float(s.replace(",", "."))
    return (to_f(c), to_f(l), to_f(a))

def cubagem_kg(c, l, a, fator=6000.0):
    """Peso cubado (kg) ≈ (C*L*A)/fator com cm."""
    return (c * l * a) / fator

def sanitize_cep(cep: str):
    """Mantém apenas 8 dígitos se válido."""
    if not cep:
        return None
    digits = re.sub(r"\D", "", cep)
    return digits if len(digits) == 8 else None

def with_retry(func, retries=1, delay=2):
    for i in range(retries + 1):
        try:
            return func()
        except Exception as e:
            if "rate limit" in str(e).lower() and i < retries:
                time.sleep(delay * (2 ** i))
                continue
            raise

# ==================== ROTEADOR DE EMBALAGEM (regras) ====================
FAMILIAS = {
    "textil": ["camiseta","blusa","moletom","calça","calca","bermuda","short","meia","roupa","body","pijama","sutiã","sutia","cueca","boné","bone"],
    "livro_papel": ["livro","caderno","planner","revista","hq","mangá","manga","papelaria","a4","a5"],
    "ceramica_vidro": ["caneca","xícara","xicara","taça","taca","vidro","garrafa","vaso","porcelana","cerâmica","ceramica"],
    "eletronicos_leves": ["fone","headset","mouse","teclado","pendrive","carregador","cabo","powerbank"],
    "cosmeticos": ["perfume","shampoo","creme","hidratante","maquiagem","batom","sérum","serum"],
    "joias_acessorios": ["colar","pulseira","anel","brinco","óculos","oculos","relógio","relogio"],
}

# (C,L,A) estimadas em cm
ESTIMATIVAS_DIM = {
    ("textil","camiseta_s"): (26, 20, 3),
    ("textil","camiseta_m"): (28, 22, 3),
    ("textil","camiseta_l"): (30, 23, 3),
    ("textil","moletom"):    (32, 25, 8),
    ("textil","calca"):      (32, 24, 5),
    ("livro_papel","livro_a5"): (22, 16, 4),
    ("livro_papel","livro_a4"): (31, 23, 4),
    ("livro_papel","caderno_a5"): (22, 16, 3),
    ("livro_papel","caderno_a4"): (31, 23, 3),
    ("cosmeticos","frasco_peq"): (12, 6, 6),
    ("cosmeticos","frasco_med"): (16, 8, 8),
    ("eletronicos_leves","fone"): (12, 12, 6),
    ("eletronicos_leves","mouse"): (14, 10, 7),
    ("joias_acessorios","oculos"): (18, 8, 6),
}

def classificar_familia(produto: str) -> str:
    p = (produto or "").lower()
    for fam, kws in FAMILIAS.items():
        if any(k in p for k in kws):
            return fam
    return "outros"

def roteador_tipo_embalagem(produto: str, fragilidade: str, qtd: int) -> str:
    fam = classificar_familia(produto)
    fr = (fragilidade or "Média").lower()
    if fam == "textil":
        return "Envelope de segurança (plástico coextrusado)"
    if fam == "livro_papel":
        return "Envelope rígido + reforço (ou caixa baixa se >1 un)"
    if fam == "ceramica_vidro":
        return "Caixa de papelão + proteção interna (múltiplas camadas)"
    if fam == "eletronicos_leves":
        return "Caixa de papelão pequena + acolchoamento"
    if fam == "cosmeticos":
        return "Caixa de papelão pequena (ou blister) + proteção pontual"
    if fam == "joias_acessorios":
        return "Caixa pequena (ou estojo) + envelope externo"
    return "Caixa de papelão padrão"

def estimar_dimensoes_se_necessario(produto: str, familia: str, tamanho_roupa: str|None=None) -> tuple[int,int,int]:
    fam = familia or classificar_familia(produto)
    p = (produto or "").lower()
    if fam == "textil":
        t = (tamanho_roupa or "").lower()
        if "moletom" in p: return ESTIMATIVAS_DIM[("textil","moletom")]
        if "calça" in p or "calca" in p: return ESTIMATIVAS_DIM[("textil","calca")]
        if t in ["p","pp","xs","s"]: return ESTIMATIVAS_DIM[("textil","camiseta_s")]
        if t in ["m"]: return ESTIMATIVAS_DIM[("textil","camiseta_m")]
        if t in ["g","gg","l","xl","xxl"]: return ESTIMATIVAS_DIM[("textil","camiseta_l")]
        return ESTIMATIVAS_DIM[("textil","camiseta_m")]
    if fam == "livro_papel":
        if "a4" in p or "caderno grande" in p: return ESTIMATIVAS_DIM[("livro_papel","livro_a4")]
        return ESTIMATIVAS_DIM[("livro_papel","livro_a5")]
    if fam == "cosmeticos":
        if any(k in p for k in ["100ml","200ml","médio","medio"]): return ESTIMATIVAS_DIM[("cosmeticos","frasco_med")]
        return ESTIMATIVAS_DIM[("cosmeticos","frasco_peq")]
    if fam == "eletronicos_leves":
        if "mouse" in p: return ESTIMATIVAS_DIM[("eletronicos_leves","mouse")]
        if any(k in p for k in ["fone","earbud"]): return ESTIMATIVAS_DIM[("eletronicos_leves","fone")]
        return (18, 12, 8)
    if fam == "joias_acessorios":
        if any(k in p for k in ["óculos","oculos"]): return ESTIMATIVAS_DIM[("joias_acessorios","oculos")]
        return (10, 10, 5)
    return (20, 15, 10)

def expandir_dimensoes_para_embalagem(c, l, a, tipo: str) -> tuple[int,int,int]:
    if "envelope" in tipo.lower():
        return (int(round(c)), int(round(l)), max(3, int(round(a+1))))
    return (int(round(c+2)), int(round(l+2)), int(round(a+2)))

# ==================== OPENAI (Consultor) ====================
def call_consultor_ia(payload: dict, tipo_preferido: str, embalagem_hint: str, model: str = "gpt-4o-mini"):
    """Chama OpenAI pedindo JSON estrito com recomendações de EMBALAGEM."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não encontrado no servidor.")
    client = OpenAI(api_key=OPENAI_API_KEY)

    system_prompt = (
        "Você é o Consultor de Embalagens da SuperFrete. "
        "Responda SEMPRE em PT-BR, didático e direto. "
        "Sua resposta DEVE ser APENAS um JSON válido seguindo exatamente o schema fornecido. "
        "Não invente políticas específicas de transportadoras. "
        "Prefira custo baixo, segurança adequada e redução de cubagem. "
        "Quando houver trade-offs, explique resumidamente."
    )

    user_prompt = f"""
Dados do lojista:
- Categoria: {payload.get('categoria')}
- Produto: {payload.get('produto')}
- Fragilidade: {payload.get('fragilidade')}
- Dimensões do item (cm): {payload.get('dimensoes_cm')} (CxLxA)
- Peso do item (kg): {payload.get('peso_kg')}
- Quantidade por envio: {payload.get('qtd_por_envio')}
- Dores: {', '.join(payload.get('dores', [])) if payload.get('dores') else ''}

Diretriz de embalagem (preferência forte):
- Tipo preferido: {tipo_preferido}
- Dimensões sugeridas da embalagem: {embalagem_hint}

Tarefa: gere recomendações de embalagem respeitando a diretriz acima.
Você pode discordar do tipo apenas se houver risco claro (ex.: fragilidade alta incompatível), mas PRECISA justificar.

Schema de saída (JSON estrito):
{{
  "caixa_recomendada": {{"descricao": "string", "dimensoes_cm": "CxLxA", "justificativa": "string"}},
  "protecao_interna": [{{"tipo": "string", "qtde_sugerida": "string", "observacao": "string"}}],
  "lacres_e_reforcos": [{{"tipo": "string", "observacao": "string"}}],
  "riscos_e_mitigacoes": [{{"risco": "string", "mitigacao": "string"}}],
  "impacto_cubagem": {{"comentario": "string"}},
  "boas_praticas": ["string", "string", "string"],
  "resumo_curto": "string"
}}

Restrições:
- Retorne APENAS o JSON do schema acima.
- Se faltarem dados, assuma o conservador e indique no campo 'justificativa' ou 'comentario'.
"""

    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = resp.choices[0].message.content
    return json.loads(content)

# ==================== SUPERFRETE (Cotação opcional, normalizada) ====================
def call_superfrete_calculator(token, user_agent_email, cep_from, cep_to,
                               length_cm, width_cm, height_cm, weight_kg,
                               services="1,2,17", use_sandbox=False):
    """
    Produção: https://api.superfrete.com/api/v0/calculator
    Sandbox : https://sandbox.superfrete.com/api/v0/calculator

    Aceita resposta como dict OU list e normaliza para:
      offer = {company, service, price, days}
    """
    base = "https://sandbox.superfrete.com" if use_sandbox else "https://api.superfrete.com"
    url = f"{base}/api/v0/calculator"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": f"SuperFrete-Consultor/1.0 ({user_agent_email})"
    }
    body = {
        "from": {"postal_code": cep_from},
        "to": {"postal_code": cep_to},
        "services": services,
        "options": {
            "own_hand": False,
            "receipt": False,
            "insurance_value": 0,
            "use_insurance_value": False
        },
        "package": {
            "height": int(round(height_cm)),
            "width":  int(round(width_cm)),
            "length": int(round(length_cm)),
            "weight": round(float(weight_kg), 3)
        }
    }

    def _norm_offer(o):
        company_name = "-"
        comp = o.get("company") if isinstance(o, dict) else None
        if isinstance(comp, dict):
            company_name = comp.get("name") or comp.get("company") or "-"
        elif isinstance(comp, str):
            company_name = comp

        service = o.get("service") or o.get("service_name") or "-"

        raw_price = o.get("price", None)
        if raw_price is None:
            raw_price = o.get("total", None)
        if raw_price is None:
            raw_price = o.get("value", 0)
        try:
            price = float(raw_price)
        except Exception:
            price = 0.0

        days = None
        dt = o.get("delivery_time") or o.get("delivery") or o.get("deadline")
        if isinstance(dt, dict):
            days = dt.get("days") or dt.get("estimate") or dt.get("min")
        elif isinstance(dt, (int, float)):
            days = int(dt)

        return {
            "company": company_name or "-",
            "service": service or "-",
            "price": price,
            "days": None if days in (None, "", "-") else int(days),
            "_raw": o,
        }

    try:
        r = requests.post(url, headers=headers, json=body, timeout=20)
        if r.status_code == 401:
            return {"error": "Token inválido/expirado (401). Gere um novo e configure SUPERFRETE_API_TOKEN."}
        if r.status_code >= 400:
            return {"error": f"Erro {r.status_code}: {r.text}"}

        data = r.json()
        if isinstance(data, list):
            ofertas = data
        elif isinstance(data, dict):
            ofertas = data.get("data") or data.get("quotes") or data.get("results") or []
        else:
            ofertas = []

        if not isinstance(ofertas, list) or not ofertas:
            return {"error": "Nenhuma oferta de frete retornada."}

        norm = []
        for o in ofertas:
            if isinstance(o, dict):
                norm.append(_norm_offer(o))
        if not norm:
            return {"error": "Formato inesperado nas ofertas."}

        best_price = min(norm, key=lambda x: (x["price"] if x["price"] > 0 else 1e12))
        best_time  = min(norm, key=lambda x: (x["days"] if isinstance(x["days"], int) else 1e9))

        return {"best_price": best_price, "best_time": best_time, "offers": norm}

    except requests.RequestException as e:
        return {"error": f"Falha de rede: {e}"}

# ==================== UI ====================
st.markdown("## 📦 Consultor de Embalagens (MVP)")
st.markdown("Preencha os dados do **item**. A IA recomendará a **embalagem** e, se você informar os CEPs, exibiremos a **estimativa de frete** com base na embalagem sugerida.")

with st.form("form"):
    col1, col2 = st.columns(2)
    with col1:
        cep_from = st.text_input("CEP de origem (obrigatório)", placeholder="ex.: 01001-000")
        categoria = st.selectbox(
            "Categoria do produto",
            ["Moda", "Papelaria", "Eletrônicos leves", "Cosméticos", "Artesanato", "Outros"],
            index=1
        )
        produto = st.text_input("Descreva o produto (ex.: camiseta, caneca, livro A5, mouse)")
        fragilidade = st.selectbox("Fragilidade", ["Baixa", "Média", "Alta"], index=1)
    with col2:
        cep_to = st.text_input("CEP de destino (opcional)", placeholder="ex.: 20040-000")
        dim = st.text_input("Dimensões do item (CxLxA em cm)", placeholder="ex.: 20x15x10")
        peso = st.number_input("Peso do item (kg)", min_value=0.0, max_value=100.0, value=0.3, step=0.01, format="%.2f")
        qtd = st.number_input("Quantidade por envio", min_value=1, max_value=50, value=1, step=1)

    # "Não sei as dimensões"
    nao_sei_dim = st.checkbox("Não sei as dimensões do item")
    tamanho_roupa = None
    if nao_sei_dim:
        st.caption("Sem problemas! Vamos estimar com base no tipo/tamanho do produto.")
        if classificar_familia(produto) == "textil":
            tsel = st.selectbox("Tamanho (se for roupa)", ["P/PP", "M", "G/GG", "Não sei"], index=1)
            if tsel == "P/PP": tamanho_roupa = "P"
            elif tsel == "G/GG": tamanho_roupa = "G"
            elif tsel == "M": tamanho_roupa = "M"
            else: tamanho_roupa = None

    dores = st.multiselect(
        "Principais dores (opcional)",
        ["Avarias", "Extravio", "Devoluções", "Volume/cubagem", "Custo de embalagem"]
    )

    submitted = st.form_submit_button("Gerar recomendação")

if submitted:
    # ---- validações
    cep_from_s = sanitize_cep(cep_from)
    if not cep_from_s:
        st.error("Informe um **CEP de origem** válido (8 dígitos).")
        st.stop()

    if nao_sei_dim:
        fam = classificar_familia(produto)
        dims_item = estimar_dimensoes_se_necessario(produto, fam, tamanho_roupa)
    else:
        dims_item = parse_dimensions(dim)

    if not dims_item:
        st.error("Informe CxLxA em cm (ex.: 20x15x10) ou marque 'Não sei as dimensões'.")
        st.stop()

    c, l, a = dims_item
    cubado_item = cubagem_kg(c, l, a, fator=6000.0)

    # Roteia tipo e estima embalagem com folga
    tipo_preferido = roteador_tipo_embalagem(produto, fragilidade, int(qtd))
    Cc_sug, Ll_sug, Aa_sug = expandir_dimensoes_para_embalagem(c, l, a, tipo_preferido)
    embalagem_hint = f"{Cc_sug}x{Ll_sug}x{Aa_sug}"

    payload = {
        "categoria": categoria,
        "produto": produto or "Produto não informado",
        "fragilidade": fragilidade,
        "dimensoes_cm": f"{int(c)}x{int(l)}x{int(a)}",
        "peso_kg": round(peso, 3),
        "qtd_por_envio": int(qtd),
        "dores": dores,
        "peso_cubado_kg": round(cubado_item, 3),
    }

    # ===== IA =====
    with st.spinner("Gerando recomendação com IA..."):
        try:
            result = with_retry(lambda: call_consultor_ia(payload, tipo_preferido, embalagem_hint))
        except Exception as e:
            emsg = str(e).lower()
            if "insufficient_quota" in emsg or ("429" in emsg and "quota" in emsg):
                st.error("Sem créditos na OpenAI agora. Verifique Billing/Usage e a variável OPENAI_API_KEY.")
            elif "rate limit" in emsg or "429" in emsg:
                st.warning("Muitos pedidos. Aguarde alguns segundos e tente novamente.")
            elif isinstance(e, JSONDecodeError) or "invalid json" in emsg:
                st.error("A IA retornou um formato inesperado. Tente novamente.")
            else:
                st.error("Não consegui concluir sua recomendação agora. Tente novamente.")
            st.stop()

    # ===== RESPOSTA (1 coluna) =====
    if result.get("resumo_curto"):
        st.markdown("### ✅ Resumo")
        st.write(result["resumo_curto"])

    # 1) Embalagem recomendada
    st.markdown("### 📦 Embalagem recomendada")
    caixa = result.get("caixa_recomendada", {}) or {}
    # Se a IA não preencher, use o hint do roteador
    desc = caixa.get("descricao") or tipo_preferido
    dims_txt = caixa.get("dimensoes_cm") or embalagem_hint
    st.write(f"**Tipo:** {desc}")
    st.write(f"**Dimensões sugeridas:** {dims_txt}")
    if caixa.get("justificativa"):
        st.caption(caixa["justificativa"])

    # 2) Proteções recomendadas
    st.markdown("### 🧱 Proteções recomendadas")
    for item in result.get("protecao_interna", []):
        st.write(f"- **{item.get('tipo','')}** — {item.get('qtde_sugerida','')}")
        if item.get("observacao"):
            st.caption(item["observacao"])
    for lacre in result.get("lacres_e_reforcos", []):
        st.write(f"- **{lacre.get('tipo','')}** — {lacre.get('observacao','')}")

    # 3) Boas práticas (linha única)
    st.markdown("### 🧪 Boas práticas")
    bps = [bp for bp in result.get("boas_praticas", []) if bp]
    if bps:
        st.write(" • ".join(bps))

    # 4) Estimativa de frete (mensagem se faltar CEP destino)
    st.divider()
    st.markdown("### 🚚 Estimativa de frete (SuperFrete)")
    cep_to_s = sanitize_cep(cep_to)

    if not cep_to_s:
        st.info("Você ainda não informou o **CEP de destino**. Preencha para ver preço e prazo.")
    elif not SUPERFRETE_API_TOKEN:
        st.warning("Token da SuperFrete não configurado no servidor (SUPERFRETE_API_TOKEN).")
    else:
        # Dimensões usadas na COTAÇÃO = EMBALAGEM recomendada (fallback: hint/roteador)
        dims_caixa = parse_dimensions(dims_txt) or (Cc_sug, Ll_sug, Aa_sug)
        Cc, Ll, Aa = dims_caixa

        # Peso p/ cotação: conservador (peso real + 50g) vs cubado da EMBALAGEM
        peso_extra = 0.05
        peso_real_final = float(peso) + peso_extra
        cubado_caixa = cubagem_kg(Cc, Ll, Aa, fator=6000.0)
        peso_para_cotacao = max(peso_real_final, cubado_caixa)

        with st.spinner("Consultando fretes na SuperFrete..."):
            cot = call_superfrete_calculator(
                token=SUPERFRETE_API_TOKEN,
                user_agent_email=SUPERFRETE_CONTACT_EMAIL,
                cep_from=cep_from_s,
                cep_to=cep_to_s,
                length_cm=Cc,
                width_cm=Ll,
                height_cm=Aa,
                weight_kg=peso_para_cotacao,
                services=SUPERFRETE_SERVICES,
                use_sandbox=SUPERFRETE_USE_SANDBOX
            )

        if cot.get("error"):
            st.error(cot["error"])
        else:
            bp = cot["best_price"]
            bt = cot["best_time"]
            st.write("**Melhor preço**")
            st.write(
                f"- {bp['company']} {bp['service']}: "
                f"**R${bp['price']:.2f}** | prazo **{bp['days'] if bp['days'] is not None else '-'}** dia(s)"
            )
            st.write("**Melhor prazo**")
            st.write(
                f"- {bt['company']} {bt['service']}: "
                f"**R${bt['price']:.2f}** | prazo **{bt['days'] if bt['days'] is not None else '-'}** dia(s)"
            )

    st.divider()
    st.caption(f"Peso real informado: **{float(peso):.3f} kg** | Peso cubado do item (fator 6000): **{cubado_item:.3f} kg**.")
    st.caption("Aviso: recomendações e estimativas são educativas; valide com seu fornecedor e política de envio.")
    st.link_button("Emitir seu frete com a SuperFrete", "https://web.superfrete.com/#/calcular-correios")

else:
    st.info("Preencha os campos e clique em **Gerar recomendação** para ver a embalagem ideal e (opcional) a cotação de frete.")

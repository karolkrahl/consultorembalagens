# app.py — Consultor de Embalagens (Streamlit) + cotação SuperFrete opcional
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

# ==================== OPENAI (Consultor) ====================
def call_consultor_ia(payload: dict, model: str = "gpt-4o-mini"):
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
- Dimensões do item (cm): {payload.get('dimensoes_cm')} (formato CxLxA)
- Peso do item (kg): {payload.get('peso_kg')}
- Quantidade por envio: {payload.get('qtd_por_envio')}
- Dores: {', '.join(payload.get('dores', [])) if payload.get('dores') else ''}

Tarefa: gere recomendações de embalagem.

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

# ==================== SUPERFRETE (Cotação opcional) ====================
def call_superfrete_calculator(token, user_agent_email, cep_from, cep_to,
                               length_cm, width_cm, height_cm, weight_kg,
                               services="1,2,17", use_sandbox=False):
    """
    Produção: https://api.superfrete.com/api/v0/calculator
    Sandbox : https://sandbox.superfrete.com/api/v0/calculator
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

    try:
        r = requests.post(url, headers=headers, json=body, timeout=20)
        if r.status_code == 401:
            return {"error": "Token inválido/expirado (401). Gere um novo e configure SUPERFRETE_API_TOKEN."}
        if r.status_code >= 400:
            return {"error": f"Erro {r.status_code}: {r.text}"}
        data = r.json()
        ofertas = data.get("data") or data
        if not ofertas or not isinstance(ofertas, list):
            return {"error": "Resposta inesperada da API de frete."}
        best_price = min(ofertas, key=lambda x: float(x.get("price", 9e9)))
        best_time  = min(ofertas, key=lambda x: int((x.get("delivery_time") or {}).get("days", 9e9)))
        return {"best_price": best_price, "best_time": best_time, "raw": ofertas}
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
        produto = st.text_input("Descreva o produto (ex.: caneca de porcelana, caderno A5, camiseta)")
        fragilidade = st.selectbox("Fragilidade", ["Baixa", "Média", "Alta"], index=2)
    with col2:
        cep_to = st.text_input("CEP de destino (opcional)", placeholder="ex.: 20040-000")
        dim = st.text_input("Dimensões do item (CxLxA em cm)", placeholder="ex.: 20x15x10")
        peso = st.number_input("Peso do item (kg)", min_value=0.0, max_value=100.0, value=0.3, step=0.01, format="%.2f")
        qtd = st.number_input("Quantidade por envio", min_value=1, max_value=50, value=1, step=1)

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

    dims_item = parse_dimensions(dim)
    if not dims_item:
        st.error("Informe as dimensões no formato CxLxA, por exemplo: 20x15x10")
        st.stop()

    c, l, a = dims_item
    cubado_item = cubagem_kg(c, l, a, fator=6000.0)

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

    with st.spinner("Gerando recomendação com IA..."):
        try:
            result = with_retry(lambda: call_consultor_ia(payload))
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
    st.write(f"**Tipo:** {caixa.get('descricao', '-')}")
    st.write(f"**Dimensões sugeridas:** {caixa.get('dimensoes_cm', '-')}")
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
        # Dimensões usadas na COTAÇÃO = EMBALAGEM recomendada (fallback: item)
        dims_caixa = parse_dimensions(caixa.get("dimensoes_cm", "")) or (c, l, a)
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
                f"- {bp.get('company',{}).get('name','-')} {bp.get('service','')}: "
                f"**R${float(bp.get('price',0)):.2f}** | prazo "
                f"**{(bp.get('delivery_time') or {}).get('days','-')}** dia(s)"
            )
            st.write("**Melhor prazo**")
            st.write(
                f"- {bt.get('company',{}).get('name','-')} {bt.get('service','')}: "
                f"**R${float(bt.get('price',0)):.2f}** | prazo "
                f"**{(bt.get('delivery_time') or {}).get('days','-')}** dia(s)"
            )

    st.divider()
    st.caption(f"Peso real informado: **{peso:.3f} kg** | Peso cubado do item (fator 6000): **{cubado_item:.3f} kg**.")
    st.caption("Aviso: recomendações e estimativas são educativas; valide com seu fornecedor e política de envio.")
    st.link_button("Emitir seu frete com a SuperFrete", "https://web.superfrete.com/#/calcular-correios")

else:
    st.info("Preencha os campos e clique em **Gerar recomendação** para ver a embalagem ideal e (opcional) a cotação de frete.")

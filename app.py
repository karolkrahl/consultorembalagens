import os
import requests
import streamlit as st

# =============================
# Variáveis de ambiente
# =============================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SUPERFRETE_API_TOKEN = os.getenv("SUPERFRETE_API_TOKEN", "")
SUPERFRETE_CONTACT_EMAIL = os.getenv("SUPERFRETE_CONTACT_EMAIL", "")
SUPERFRETE_USE_SANDBOX = os.getenv("SUPERFRETE_USE_SANDBOX", "false").lower() == "true"
SUPERFRETE_SERVICES = ["correios", "jadlog", "loggi"]

# =============================
# Funções auxiliares
# =============================
def sanitize_cep(cep: str) -> str:
    """Remove caracteres não numéricos de um CEP."""
    return "".join(c for c in str(cep) if c.isdigit())

def parse_dimensions(dim_str: str):
    """Recebe string tipo '20x15x10' e retorna (C,L,A) em cm."""
    try:
        partes = [float(x) for x in dim_str.lower().replace("cm", "").split("x")]
        if len(partes) == 3:
            return tuple(partes)
    except Exception:
        return None
    return None

def cubagem_kg(c, l, a, fator=6000.0):
    """Calcula peso cubado em kg."""
    return (c * l * a) / fator

def call_superfrete_calculator(token, user_agent_email, cep_from, cep_to, length_cm, width_cm, height_cm, weight_kg, services, use_sandbox=False):
    """Chama API de cotação da SuperFrete."""
    base_url = "https://sandbox-api.superfrete.com/v0" if use_sandbox else "https://api.superfrete.com/v0"
    url = f"{base_url}/quote"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": f"SuperFrete-Demo-App ({user_agent_email})" if user_agent_email else "SuperFrete-Demo-App"
    }

    payload = {
        "from": {"zipcode": cep_from},
        "to": {"zipcode": cep_to},
        "products": [
            {
                "weight": weight_kg,
                "length": length_cm,
                "width": width_cm,
                "height": height_cm,
                "quantity": 1,
            }
        ],
        "services": services,
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()

        if "errors" in data:
            return {"error": str(data["errors"])}

        quotes = data.get("quotes", [])
        if not quotes:
            return {"error": "Nenhum frete retornado pela SuperFrete."}

        best_price = min(quotes, key=lambda q: float(q.get("price", 1e9)))
        best_time = min(quotes, key=lambda q: (q.get("delivery_time") or {}).get("days", 1e9))

        return {"best_price": best_price, "best_time": best_time}

    except Exception as e:
        return {"error": str(e)}

# =============================
# Estilo customizado
# =============================
st.markdown(
    """
    <style>
    body, .stApp {
        font-family: 'Poppins', sans-serif;
        background-color: #f9f9f9;
    }
    h1, h2, h3 {
        color: #0fae79;
    }
    .stButton>button {
        background-color: #0fae79;
        color: #fff;
        border-radius: 6px;
        padding: 8px 16px;
        border: none;
    }
    .stButton>button:hover {
        background-color: #0c8a61;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =============================
# Layout principal
# =============================
st.title("📦 Cotador de Fretes - SuperFrete")

st.write("Preencha os dados abaixo para calcular o melhor preço e prazo de frete via **SuperFrete**.")

# Inputs
cep_from = st.text_input("CEP de origem", "01001000")
cep_to = st.text_input("CEP de destino", "")
peso = st.number_input("Peso (kg)", value=0.3, step=0.1, format="%.2f")
dim_str = st.text_input("Dimensões do produto (C x L x A cm)", "20x15x10")

caixa = {"dimensoes_cm": "20x15x10"}  # Pode ser substituído por seleção dinâmica

# =============================
# Estimativa de frete
# =============================
st.divider()
st.markdown("### 🚚 Estimativa de frete (SuperFrete)")

cep_to_s = sanitize_cep(cep_to)
cep_from_s = sanitize_cep(cep_from)

if not cep_to_s:
    st.info("Você ainda não informou o **CEP de destino**. Preencha para ver preço e prazo.")
elif not SUPERFRETE_API_TOKEN:
    st.warning("Token da SuperFrete não configurado no servidor (SUPERFRETE_API_TOKEN).")
else:
    dims_caixa = parse_dimensions(caixa.get("dimensoes_cm", "")) or parse_dimensions(dim_str) or (20, 15, 10)
    Cc, Ll, Aa = dims_caixa

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

# =============================
# Rodapé
# =============================
st.divider()
st.caption("⚡ Powered by SuperFrete | Demo App com Streamlit")

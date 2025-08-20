import os
import requests
from flask import Flask, request, render_template_string

app = Flask(__name__)

# ===== Variáveis de ambiente =====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPERFRETE_API_TOKEN = os.getenv("SUPERFRETE_API_TOKEN")
SUPERFRETE_CONTACT_EMAIL = os.getenv("SUPERFRETE_CONTACT_EMAIL", "contato@seudominio.com")

# ===== Template HTML (1 coluna, mais limpo) =====
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Assistente de Embalagem e Frete</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 0;
            background: #f5f7fa;
            color: #333;
        }
        .container {
            max-width: 700px;
            margin: 40px auto;
            padding: 30px;
            background: #fff;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        }
        h1 {
            text-align: center;
            color: #0fae79;
            margin-bottom: 20px;
        }
        form {
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        label {
            font-weight: bold;
            margin-bottom: 4px;
        }
        input, select {
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 14px;
            width: 100%;
        }
        button {
            background: #0fae79;
            color: #fff;
            border: none;
            padding: 12px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 16px;
            margin-top: 10px;
        }
        button:hover {
            background: #0c8c63;
        }
        .resultado {
            margin-top: 30px;
            padding: 20px;
            background: #eefaf5;
            border-left: 4px solid #0fae79;
            border-radius: 6px;
            white-space: pre-line;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Assistente de Embalagem e Frete</h1>
        <form method="post">
            <label>Categoria do produto:</label>
            <input type="text" name="categoria" required>

            <label>Dimensões (cm):</label>
            <input type="number" step="0.1" name="comprimento" placeholder="Comprimento" required>
            <input type="number" step="0.1" name="largura" placeholder="Largura" required>
            <input type="number" step="0.1" name="altura" placeholder="Altura" required>

            <label>Peso (kg):</label>
            <input type="number" step="0.01" name="peso" required>

            <label>Fragilidade:</label>
            <select name="fragilidade">
                <option value="baixa">Baixa</option>
                <option value="média">Média</option>
                <option value="alta">Alta</option>
            </select>

            <label>CEP de origem:</label>
            <input type="text" name="cep_origem" required>

            <label>CEP de destino (opcional):</label>
            <input type="text" name="cep_destino">

            <button type="submit">Calcular</button>
        </form>

        {% if resultado %}
        <div class="resultado">
            <h3>Resultado:</h3>
            {{ resultado }}
        </div>
        {% endif %}
    </div>
</body>
</html>
"""

# ===== Função para consumir API da SuperFrete =====
def calcular_frete(cep_origem, cep_destino, dimensoes, peso):
    url = "https://api.superfrete.com/v0/quote"

    payload = {
        "from": {"postal_code": cep_origem},
        "to": {"postal_code": cep_destino},
        "package": {
            "height": dimensoes["altura"],
            "width": dimensoes["largura"],
            "length": dimensoes["comprimento"],
            "weight": peso
        }
    }

    headers = {
        "Authorization": f"Bearer {SUPERFRETE_API_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": f"AppFrete/1.0 ({SUPERFRETE_CONTACT_EMAIL})"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "quotes" in data and len(data["quotes"]) > 0:
            melhor_opcao = min(data["quotes"], key=lambda x: x.get("price", float("inf")))
            return f"{melhor_opcao['company']['name']} - R$ {melhor_opcao['price']} - {melhor_opcao['delivery_time']} dias"
        else:
            return "Nenhuma opção de frete encontrada."
    except Exception as e:
        return f"Erro ao calcular frete: {e}"

# ===== Rotas Flask =====
@app.route("/", methods=["GET", "POST"])
def index():
    resultado = None
    if request.method == "POST":
        categoria = request.form["categoria"]
        comprimento = float(request.form["comprimento"])
        largura = float(request.form["largura"])
        altura = float(request.form["altura"])
        peso = float(request.form["peso"])
        fragilidade = request.form["fragilidade"]
        cep_origem = request.form["cep_origem"]
        cep_destino = request.form.get("cep_destino", "").strip()

        # Embalagem recomendada (simplesmente ajusta +2 cm em cada dimensão)
        embalagem = {
            "comprimento": comprimento + 2,
            "largura": largura + 2,
            "altura": altura + 2
        }

        # Proteções recomendadas
        protecoes = "Plástico bolha interno" if fragilidade != "baixa" else "Proteção simples"
        lacres = "Reforçar com fita adesiva"

        boas_praticas = "Identifique a embalagem e preencha a nota fiscal corretamente."

        resultado = f"""
📦 Embalagem recomendada: Caixa {embalagem['comprimento']} x {embalagem['largura']} x {embalagem['altura']} cm
🛡️ Proteções recomendadas: {protecoes} + {lacres}
✅ Boas práticas: {boas_praticas}
"""

        # Frete (se tiver destino)
        if cep_destino:
            frete = calcular_frete(cep_origem, cep_destino, embalagem, peso)
            resultado += f"\n🚚 Estimativa de frete: {frete}\n\n👉 Emitir frete com desconto: https://web.superfrete.com/#/calcular-correios"
        else:
            resultado += "\n🚚 Para estimar o frete, informe também o CEP de destino."

    return render_template_string(HTML_TEMPLATE, resultado=resultado)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

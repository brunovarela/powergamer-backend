# 🧠 Tibia Tracker API

API construída com [FastAPI](https://fastapi.tiangolo.com/) para rastrear a evolução diária dos jogadores no ranking de um servidor OTServer (como Rexis). A aplicação realiza scraping da página de highscores, salva snapshots diários dos jogadores e calcula ganhos de nível e experiência.

---

## 📦 Funcionalidades

- Scraping automático diário dos highscores do servidor.
- Armazenamento de snapshots diários em banco SQLite.
- Cálculo dos ganhos diários de experiência e níveis.
- Histórico individual por jogador.
- Ranking dos maiores "farmers" em XP dos últimos N dias.
- API RESTful com endpoints para acesso a todos os dados.

---

## 🚀 Requisitos

- Python 3.8+
- Pip

---

## 📚 Instalação

```bash
# Clone o repositório
git clone https://github.com/seuusuario/powergamer-backend.git
cd powergamer-backend/app/

# Instale as dependências
pip install -r requirements.txt
```

---

## 📁 Estrutura do Projeto

```
main.py              # Arquivo principal com API e scraping
tibia_tracker.db     # Banco SQLite gerado automaticamente
```

---

## ⚙️ Execução

### Local
```bash
python main.py
```

A API estará disponível em `http://localhost:8000`.

### Com Uvicorn (recomendado)
```bash
uvicorn main:app --reload
```

---

## ⏰ Tarefa Agendada

- Um scheduler é iniciado automaticamente com a API e executa o scraping diariamente às **00:01**.
- Também é possível acionar a coleta manualmente pelo endpoint `/scrape/manual`.

---

## 🔌 Endpoints Disponíveis

### 🔹 `GET /`
Retorna informações básicas da API.

### 🔹 `GET /players/current`
Retorna o ranking atual (último snapshot salvo).

### 🔹 `GET /players/daily-gains?date=YYYY-MM-DD`
Retorna os ganhos de XP e nível do dia informado. Se nenhum `date` for passado, retorna os dados do dia atual.

### 🔹 `GET /players/{player_name}/history?days=N`
Retorna o histórico de um jogador nos últimos `N` dias (default = 7).

### 🔹 `POST /scrape/manual`
Inicia manualmente o scraping e cálculo de ganhos diários em background.

### 🔹 `GET /stats/top-gainers?days=N`
Retorna os 20 jogadores que mais ganharam experiência nos últimos `N` dias (default = 7).

---

## 🗃️ Banco de Dados

O banco `tibia_tracker.db` é criado automaticamente com duas tabelas principais:

- **player_snapshots**: Armazena o ranking diário dos jogadores.
- **daily_gains**: Guarda os ganhos calculados entre snapshots consecutivos.

---

## 🌐 Scraping

O scraping é feito no site `https://rexis.soerpg.com/sub.php?page=highscores`, utilizando `requests` e `BeautifulSoup`. O sistema é robusto para lidar com erros e mudanças menores na estrutura da tabela.

---

## 🔐 CORS

O CORS está habilitado para todas as origens (`*`) por padrão. Para produção, ajuste o parâmetro `allow_origins` no middleware para permitir apenas domínios específicos.

---

## 📌 Exemplo de resposta

### `GET /players/daily-gains`
```json
[
  {
    "name": "Player 1",
    "current_level": 302,
    "current_experience": 28900000,
    "exp_gained_today": 145000,
    "level_gained_today": 2,
    "rank": 5
  }
]
```

---

## 🧑‍💻 Autor

Bruno Varela  
Projeto: Tibia Powergamer Tracker

---

## 📃 Licença

Este projeto é open-source e está disponível sob a licença MIT.

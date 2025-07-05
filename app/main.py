from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import requests
from bs4 import BeautifulSoup
import schedule
import time
from datetime import datetime, timedelta
import threading
from contextlib import contextmanager
import re

app = FastAPI(title="Tibia Tracker API", version="1.0.0")

# CORS middleware para permitir requisições do frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Em produção, especificar domínios
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Models Pydantic
class Player(BaseModel):
    rank: int
    name: str
    level: int
    experience: int
    vocation: str

class PlayerHistory(BaseModel):
    date: str
    level: int
    experience: int
    exp_gained: int
    level_gained: int

class DailyGains(BaseModel):
    name: str
    current_level: int
    current_experience: int
    exp_gained_today: int
    level_gained_today: int
    rank: int

# Database setup
DATABASE_URL = "tibia_tracker.db"

def init_database():
    """Inicializa o banco de dados com as tabelas necessárias"""
    with sqlite3.connect(DATABASE_URL) as conn:
        cursor = conn.cursor()
        
        # Tabela de players (snapshot diário)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS player_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                level INTEGER NOT NULL,
                experience INTEGER NOT NULL,
                vocation TEXT,
                rank_position INTEGER,
                date DATE NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, date)
            )
        """)
        
        # Tabela de ganhos diários calculados
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_gains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                date DATE NOT NULL,
                exp_gained INTEGER DEFAULT 0,
                level_gained INTEGER DEFAULT 0,
                starting_level INTEGER,
                ending_level INTEGER,
                starting_exp INTEGER,
                ending_exp INTEGER,
                UNIQUE(name, date)
            )
        """)
        
        conn.commit()

@contextmanager
def get_db_connection():
    """Context manager para conexões com o banco"""
    conn = sqlite3.connect(DATABASE_URL)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

class TibiaTracker:
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def scrape_highscores(self) -> List[Player]:
        """Scraping da página de highscores do Rexis - CORRIGIDO"""
        try:
            response = requests.get(self.server_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            players = []
            
            # Encontrar a tabela de ranking específica do Rexis
            table = soup.find('table', class_='Table3')
            
            if not table:
                print("Tabela de ranking não encontrada!")
                return []
            
            # Pegar todas as linhas da tabela (exceto o cabeçalho)
            rows = table.find_all('tr')[1:]  # Pula o cabeçalho
            
            for row in rows:
                try:
                    cells = row.find_all('td')
                    
                    if len(cells) >= 6:  # Estrutura: #, outfit, name, vocation, level, experience
                        # Posição no ranking (célula 0)
                        rank_text = cells[0].get_text(strip=True).replace('.', '')
                        rank = int(rank_text) if rank_text.isdigit() else 0
                        
                        # Nome do player (célula 2 - dentro do link)
                        name_link = cells[2].find('a')
                        if name_link:
                            name = name_link.get_text(strip=True)
                        else:
                            name = cells[2].get_text(strip=True)
                        
                        # Vocação (célula 3)
                        vocation = cells[3].get_text(strip=True)
                        
                        # Level (célula 4)
                        level_text = cells[4].get_text(strip=True)
                        level = int(level_text) if level_text.isdigit() else 0
                        
                        # Experiência (célula 5)
                        exp_text = cells[5].get_text(strip=True)
                        # Remove qualquer formatação e converte para int
                        experience = int(re.sub(r'[^\d]', '', exp_text)) if exp_text else 0
                        
                        # Só adiciona se tiver dados válidos
                        if name and rank > 0:
                            players.append(Player(
                                rank=rank,
                                name=name,
                                level=level,
                                experience=experience,
                                vocation=vocation
                            ))
                            
                except (ValueError, IndexError, AttributeError) as e:
                    print(f"Erro ao processar linha do ranking: {e}")
                    continue
            
            print(f"Scraping concluído: {len(players)} players encontrados")
            return players
            
        except requests.RequestException as e:
            print(f"Erro ao acessar {self.server_url}: {e}")
            return []
        except Exception as e:
            print(f"Erro no scraping: {e}")
            return []
    
    def save_daily_snapshot(self, players: List[Player]):
        """Salva snapshot diário dos players"""
        today = datetime.now().date()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            for player in players:
                cursor.execute("""
                    INSERT OR REPLACE INTO player_snapshots 
                    (name, level, experience, vocation, rank_position, date)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (player.name, player.level, player.experience, 
                      player.vocation, player.rank, today))
            
            conn.commit()
            print(f"Snapshot salvo para {len(players)} players em {today}")
    
    def calculate_daily_gains(self):
        """Calcula ganhos diários comparando com o dia anterior"""
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Busca players de hoje e ontem
            cursor.execute("""
                SELECT t.name, t.level as today_level, t.experience as today_exp,
                       y.level as yesterday_level, y.experience as yesterday_exp
                FROM player_snapshots t
                LEFT JOIN player_snapshots y ON t.name = y.name AND y.date = ?
                WHERE t.date = ?
            """, (yesterday, today))
            
            results = cursor.fetchall()
            
            for row in results:
                name = row['name']
                today_level = row['today_level']
                today_exp = row['today_exp']
                yesterday_level = row['yesterday_level'] or today_level
                yesterday_exp = row['yesterday_exp'] or today_exp
                
                exp_gained = today_exp - yesterday_exp
                level_gained = today_level - yesterday_level
                
                cursor.execute("""
                    INSERT OR REPLACE INTO daily_gains
                    (name, date, exp_gained, level_gained, starting_level, 
                     ending_level, starting_exp, ending_exp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, today, exp_gained, level_gained, yesterday_level,
                      today_level, yesterday_exp, today_exp))
            
            conn.commit()
            print(f"Ganhos diários calculados para {len(results)} players")

# Instância do tracker
tracker = TibiaTracker("https://rexis.soerpg.com/sub.php?page=highscores")

# Função para job automático
def daily_scraping_job():
    """Job que roda automaticamente para coletar dados"""
    print(f"Iniciando coleta automática - {datetime.now()}")
    players = tracker.scrape_highscores()
    
    if players:
        tracker.save_daily_snapshot(players)
        tracker.calculate_daily_gains()
        print(f"Coleta concluída: {len(players)} players processados")
    else:
        print("Nenhum player encontrado no scraping")

# Scheduler em thread separada
def run_scheduler():
    schedule.every().day.at("00:01").do(daily_scraping_job)  # 00:01 para evitar problemas de meia-noite
    
    while True:
        schedule.run_pending()
        time.sleep(60)

# Iniciar scheduler em thread separada
def start_scheduler():
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()

# API Endpoints
@app.on_event("startup")
async def startup_event():
    init_database()
    start_scheduler()
    print("Tibia Tracker API iniciada!")

@app.get("/")
async def root():
    return {"message": "Tibia Tracker API", "version": "1.0.0"}

@app.get("/players/current", response_model=List[Player])
async def get_current_ranking():
    """Retorna o ranking atual (último snapshot)"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM player_snapshots 
            WHERE date = (SELECT MAX(date) FROM player_snapshots)
            ORDER BY rank_position
        """)
        
        rows = cursor.fetchall()
        
        return [
            Player(
                rank=row['rank_position'],
                name=row['name'],
                level=row['level'],
                experience=row['experience'],
                vocation=row['vocation'] or "Unknown"
            )
            for row in rows
        ]

@app.get("/players/daily-gains", response_model=List[DailyGains])
async def get_daily_gains(date: Optional[str] = None):
    """Retorna ganhos diários dos players"""
    target_date = datetime.strptime(date, "%Y-%m-%d").date() if date else datetime.now().date()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT dg.*, ps.rank_position
            FROM daily_gains dg
            LEFT JOIN player_snapshots ps ON dg.name = ps.name AND ps.date = dg.date
            WHERE dg.date = ?
            ORDER BY dg.exp_gained DESC
        """, (target_date,))
        
        rows = cursor.fetchall()
        
        return [
            DailyGains(
                name=row['name'],
                current_level=row['ending_level'],
                current_experience=row['ending_exp'],
                exp_gained_today=row['exp_gained'],
                level_gained_today=row['level_gained'],
                rank=row['rank_position'] or 0
            )
            for row in rows
        ]

@app.get("/players/{player_name}/history", response_model=List[PlayerHistory])
async def get_player_history(player_name: str, days: int = 7):
    """Retorna histórico de um player específico"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ps.date, ps.level, ps.experience, 
                   COALESCE(dg.exp_gained, 0) as exp_gained,
                   COALESCE(dg.level_gained, 0) as level_gained
            FROM player_snapshots ps
            LEFT JOIN daily_gains dg ON ps.name = dg.name AND ps.date = dg.date
            WHERE ps.name = ?
            ORDER BY ps.date DESC
            LIMIT ?
        """, (player_name, days))
        
        rows = cursor.fetchall()
        
        return [
            PlayerHistory(
                date=row['date'],
                level=row['level'],
                experience=row['experience'],
                exp_gained=row['exp_gained'],
                level_gained=row['level_gained']
            )
            for row in rows
        ]

@app.post("/scrape/manual")
async def manual_scrape(background_tasks: BackgroundTasks):
    """Endpoint para scraping manual"""
    background_tasks.add_task(daily_scraping_job)
    return {"message": "Scraping manual iniciado em background"}

@app.get("/stats/top-gainers")
async def get_top_gainers(days: int = 7):
    """Retorna os maiores farmers dos últimos N dias"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name, 
                   SUM(exp_gained) as total_exp_gained,
                   SUM(level_gained) as total_levels_gained,
                   COUNT(*) as days_tracked,
                   AVG(exp_gained) as avg_daily_exp
            FROM daily_gains 
            WHERE date >= date('now', '-{} days')
            GROUP BY name
            ORDER BY total_exp_gained DESC
            LIMIT 20
        """.format(days))
        
        rows = cursor.fetchall()
        
        return [
            {
                "name": row['name'],
                "total_exp_gained": row['total_exp_gained'],
                "total_levels_gained": row['total_levels_gained'],
                "days_tracked": row['days_tracked'],
                "avg_daily_exp": round(row['avg_daily_exp'], 0)
            }
            for row in rows
        ]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000, 
        log_level="info",
        access_log=True
    )

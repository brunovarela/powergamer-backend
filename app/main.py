import os
import sys
import logging
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import requests
from bs4 import BeautifulSoup
import asyncio
from datetime import datetime, timedelta
from contextlib import contextmanager
import re
from pathlib import Path

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('tibia_tracker.log')
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Tibia Tracker API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Mais permissivo para testes
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

class PlayerSearch(BaseModel):
    name: str
    level: int
    experience: int
    vocation: str
    rank: int
    last_seen: str

# Database setup - usar caminho absoluto para Railway
DATABASE_PATH = Path(__file__).parent / "tibia_tracker.db"
DATABASE_URL = str(DATABASE_PATH)

def init_database():
    """Inicializa o banco de dados com as tabelas necessárias"""
    try:
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
            
            # Índices para melhorar performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_player_name ON player_snapshots(name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_player_date ON player_snapshots(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_player_rank ON player_snapshots(rank_position)")
            
            conn.commit()
            logger.info("Database initialized successfully")
            
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise

@contextmanager
def get_db_connection():
    """Context manager para conexões com o banco"""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_URL, timeout=30)
        conn.row_factory = sqlite3.Row
        yield conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

class TibiaTracker:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
    
    def scrape_page(self, page_num: int) -> List[Player]:
        """Scraping de uma página específica do ranking"""
        try:
            url = f"{self.base_url}&pag={page_num}"
            logger.info(f"Scraping page {page_num}: {url}")
            
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            players = []
            
            # Encontrar a tabela de ranking
            table = soup.find('table', class_='Table3')
            
            if not table:
                logger.warning(f"Ranking table not found on page {page_num}")
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
                        name = name_link.get_text(strip=True) if name_link else cells[2].get_text(strip=True)
                        
                        # Vocação (célula 3)
                        vocation = cells[3].get_text(strip=True)
                        
                        # Level (célula 4)
                        level_text = cells[4].get_text(strip=True)
                        level = int(level_text) if level_text.isdigit() else 0
                        
                        # Experiência (célula 5)
                        exp_text = cells[5].get_text(strip=True)
                        # Remove qualquer formatação e converte para int
                        experience = int(re.sub(r'[^\d]', '', exp_text)) if exp_text else 0
                        
                        # Validar dados
                        if name and rank > 0 and level > 0:
                            players.append(Player(
                                rank=rank,
                                name=name,
                                level=level,
                                experience=experience,
                                vocation=vocation
                            ))
                            
                except (ValueError, IndexError, AttributeError) as e:
                    logger.warning(f"Error processing row on page {page_num}: {e}")
                    continue
            
            logger.info(f"Page {page_num} processed: {len(players)} players found")
            return players
            
        except requests.RequestException as e:
            logger.error(f"Request error on page {page_num}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error scraping page {page_num}: {e}")
            return []
    
    async def scrape_all_pages_async(self, total_pages: int = 5) -> List[Player]:
        """Scraping assíncrono de todas as páginas"""
        all_players = []
        
        for page in range(1, total_pages + 1):
            logger.info(f"Scraping page {page}/{total_pages}...")
            players = self.scrape_page(page)
            all_players.extend(players)
            
            # Pausa entre requests
            await asyncio.sleep(2)
        
        logger.info(f"Scraping complete: {len(all_players)} players found")
        return all_players
    
    def save_daily_snapshot(self, players: List[Player]):
        """Salva snapshot diário dos players"""
        if not players:
            logger.warning("No players to save")
            return
            
        today = datetime.now().date()
        
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                
                saved_count = 0
                for player in players:
                    try:
                        cursor.execute("""
                            INSERT OR REPLACE INTO player_snapshots 
                            (name, level, experience, vocation, rank_position, date)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (player.name, player.level, player.experience, 
                              player.vocation, player.rank, today))
                        saved_count += 1
                    except Exception as e:
                        logger.error(f"Error saving player {player.name}: {e}")
                        continue
                
                conn.commit()
                logger.info(f"Snapshot saved for {saved_count} players on {today}")
                
        except Exception as e:
            logger.error(f"Error saving daily snapshot: {e}")
    
    def calculate_daily_gains(self):
        """Calcula ganhos diários comparando com o dia anterior"""
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        
        try:
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
                
                gains_calculated = 0
                for row in results:
                    try:
                        name = row['name']
                        today_level = row['today_level']
                        today_exp = row['today_exp']
                        yesterday_level = row['yesterday_level'] or today_level
                        yesterday_exp = row['yesterday_exp'] or today_exp
                        
                        exp_gained = max(0, today_exp - yesterday_exp)  # Evitar valores negativos
                        level_gained = max(0, today_level - yesterday_level)
                        
                        cursor.execute("""
                            INSERT OR REPLACE INTO daily_gains
                            (name, date, exp_gained, level_gained, starting_level, 
                             ending_level, starting_exp, ending_exp)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (name, today, exp_gained, level_gained, yesterday_level,
                              today_level, yesterday_exp, today_exp))
                        gains_calculated += 1
                        
                    except Exception as e:
                        logger.error(f"Error calculating gains for {row['name']}: {e}")
                        continue
                
                conn.commit()
                logger.info(f"Daily gains calculated for {gains_calculated} players")
                
        except Exception as e:
            logger.error(f"Error calculating daily gains: {e}")

# Instância do tracker
tracker = TibiaTracker("https://rexis.soerpg.com/sub.php?page=highscores")

# Função para job automático
async def daily_scraping_job():
    """Job que roda automaticamente para coletar dados"""
    logger.info(f"Starting automatic data collection - {datetime.now()}")
    
    try:
        players = await tracker.scrape_all_pages_async(5)  # Top 100 players
        
        if players:
            tracker.save_daily_snapshot(players)
            tracker.calculate_daily_gains()
            logger.info(f"Collection completed: {len(players)} players processed")
        else:
            logger.warning("No players found during scraping")
            
    except Exception as e:
        logger.error(f"Error in daily scraping job: {e}")

# API Endpoints
@app.on_event("startup")
async def startup_event():
    """Evento de inicialização da API"""
    try:
        logger.info("Starting Tibia Tracker API...")
        init_database()
        logger.info("Tibia Tracker API started successfully!")
    except Exception as e:
        logger.error(f"Failed to start API: {e}")
        raise

@app.get("/")
async def root():
    return {
        "message": "Tibia Tracker API", 
        "version": "1.0.0",
        "status": "running",
        "database": "connected" if DATABASE_PATH.exists() else "not_found"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": "connected"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service unavailable")

@app.get("/players/current", response_model=List[Player])
async def get_current_ranking(limit: int = 20):
    """Retorna o ranking atual (último snapshot)"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM player_snapshots 
                WHERE date = (SELECT MAX(date) FROM player_snapshots)
                ORDER BY rank_position
                LIMIT ?
            """, (limit,))
            
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
    except Exception as e:
        logger.error(f"Error getting current ranking: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/players/search/{player_name}", response_model=PlayerSearch)
async def search_player(player_name: str):
    """Busca um player específico"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM player_snapshots 
                WHERE LOWER(name) = LOWER(?)
                ORDER BY date DESC
                LIMIT 1
            """, (player_name,))
            
            row = cursor.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail="Player not found")
            
            return PlayerSearch(
                name=row['name'],
                level=row['level'],
                experience=row['experience'],
                vocation=row['vocation'] or "Unknown",
                rank=row['rank_position'],
                last_seen=row['date']
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error searching player {player_name}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/players/search", response_model=List[PlayerSearch])
async def search_players(name: str, limit: int = 10):
    """Busca players por nome parcial"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ps1.* FROM player_snapshots ps1
                INNER JOIN (
                    SELECT name, MAX(date) as max_date
                    FROM player_snapshots
                    WHERE LOWER(name) LIKE LOWER(?)
                    GROUP BY name
                ) ps2 ON ps1.name = ps2.name AND ps1.date = ps2.max_date
                ORDER BY ps1.rank_position
                LIMIT ?
            """, (f"%{name}%", limit))
            
            rows = cursor.fetchall()
            
            return [
                PlayerSearch(
                    name=row['name'],
                    level=row['level'],
                    experience=row['experience'],
                    vocation=row['vocation'] or "Unknown",
                    rank=row['rank_position'],
                    last_seen=row['date']
                )
                for row in rows
            ]
    except Exception as e:
        logger.error(f"Error searching players with name '{name}': {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/players/daily-gains", response_model=List[DailyGains])
async def get_daily_gains(date: Optional[str] = None):
    """Retorna ganhos diários dos players"""
    try:
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
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    except Exception as e:
        logger.error(f"Error getting daily gains: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/players/{player_name}/history", response_model=List[PlayerHistory])
async def get_player_history(player_name: str, days: int = 7):
    """Retorna histórico de um player específico"""
    try:
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
            
            if not rows:
                raise HTTPException(status_code=404, detail="Player not found")
            
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting player history for {player_name}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/scrape/manual")
async def manual_scrape(background_tasks: BackgroundTasks):
    """Endpoint para scraping manual"""
    try:
        background_tasks.add_task(daily_scraping_job)
        return {"message": "Manual scraping started in background"}
    except Exception as e:
        logger.error(f"Error starting manual scrape: {e}")
        raise HTTPException(status_code=500, detail="Failed to start manual scraping")

@app.get("/stats/top-gainers")
async def get_top_gainers(days: int = 7):
    """Retorna os maiores farmers dos últimos N dias"""
    try:
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
                LIMIT 50
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
    except Exception as e:
        logger.error(f"Error getting top gainers: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/stats/server")
async def get_server_stats():
    """Retorna estatísticas gerais do servidor"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Total de players trackados
            cursor.execute("SELECT COUNT(DISTINCT name) as total_players FROM player_snapshots")
            total_players = cursor.fetchone()['total_players']
            
            # Players ativos hoje
            today = datetime.now().date()
            cursor.execute("SELECT COUNT(*) as active_today FROM player_snapshots WHERE date = ?", (today,))
            active_today = cursor.fetchone()['active_today']
            
            # Maior level
            cursor.execute("""
                SELECT MAX(level) as highest_level
                FROM player_snapshots
                WHERE date = (SELECT MAX(date) FROM player_snapshots)
            """)
            highest_level = cursor.fetchone()['highest_level']
            
            # Maior exp ganha hoje
            cursor.execute("SELECT MAX(exp_gained) as highest_exp_today FROM daily_gains WHERE date = ?", (today,))
            highest_exp_today = cursor.fetchone()['highest_exp_today'] or 0
            
            return {
                "total_players_tracked": total_players,
                "active_players_today": active_today,
                "highest_level": highest_level,
                "highest_exp_gain_today": highest_exp_today,
                "last_update": today.isoformat()
            }
    except Exception as e:
        logger.error(f"Error getting server stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# Para produção no Railway
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port, 
        log_level="info"
    )

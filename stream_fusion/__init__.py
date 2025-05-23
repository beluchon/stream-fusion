"""stream_fusion package."""

import asyncio
from fastapi import FastAPI
from .services.postgresql.db_init import init_db

app = FastAPI()

# Initialiser la base de données au démarrage de l'application
@app.on_event("startup")
async def startup_event():
    print("🔄 Initialisation de la base de données...")
    await init_db()
    print("✅ Application prête à recevoir des requêtes")

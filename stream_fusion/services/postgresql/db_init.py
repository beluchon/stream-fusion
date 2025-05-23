"""Initialisation de la base de données."""
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .base import Base
from .models.apikey_model import APIKeyModel
from stream_fusion.settings import settings

async def init_db():
    """Initialise la base de données et crée les tables si elles n'existent pas."""
    engine = create_async_engine(str(settings.pg_url))
    
    async with engine.begin() as conn:
        # Créer les tables si elles n'existent pas
        await conn.run_sync(Base.metadata.create_all)
        
        # Vérifier si la colonne proxied_links existe
        result = await conn.execute(
            text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='api_keys' AND column_name='proxied_links'""")
        )
        
        if not result.fetchone():
            # Ajouter la colonne si elle n'existe pas
            await conn.execute(
                text("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS proxied_links BOOLEAN DEFAULT FALSE")
            )
            print("✅ Colonne 'proxied_links' ajoutée à la table 'api_keys'")
    
    await engine.dispose()
    print("✅ Base de données initialisée avec succès")

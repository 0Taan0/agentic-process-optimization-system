# core/mongo_client.py
import os
from typing import Tuple
from pymongo import MongoClient

def get_mongo() -> Tuple[MongoClient, str]:
    """
    Stellt eine Mongo-Client-Verbindung bereit und liefert (client, db_name).
    Erwartet Umgebungsvariablen:
      - MONGO_URI  (z.B. mongodb://localhost:27017)
      - MONGO_DB   (z.B. bpi_mas)
    """
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    db_name = os.getenv("MONGO_DB", "bpi_mas")
    client = MongoClient(uri, uuidRepresentation="standard")
    return client, db_name
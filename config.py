import os
from datetime import timedelta
from dotenv import load_dotenv



load_dotenv()

class Config:
    """
    Configurazione base dell'applicazione.
    Le variabili sensibili vengono lette da variabili d'ambiente per sicurezza.
    """
    
    EMAIL_USER = os.environ.get("EMAIL_USER")
    EMAIL_PASS = os.environ.get("EMAIL_PASS")
    SECRET_KEY = os.environ.get("SECRET_KEY")
    ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY") or "YOUR_API_KEY_HERE"

    
    SQLALCHEMY_DATABASE_URI = 'sqlite:///bank.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=15)
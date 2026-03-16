import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///drizl.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'jwt-secret-change-in-production')
    JWT_ACCESS_TOKEN_EXPIRES = 60 * 60 * 24 * 7  # 7 days in seconds
    SHORT_URL_BASE = os.environ.get('SHORT_URL_BASE', 'http://localhost:5000')
    FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:5000')
    DEBUG = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
    LINK_CACHE_TTL = 3600  # 1 hour Redis TTL

class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', '').replace('postgres://', 'postgresql://', 1)

config = {
    'development': Config,
    'production': ProductionConfig,
    'default': Config
}

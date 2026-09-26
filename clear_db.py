import os
import urllib.parse
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

app = Flask(__name__)

database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql+psycopg2://', 1)
    elif database_url.startswith('postgresql://'):
        database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    raw_password = "$A08138529746a"
    safe_password = urllib.parse.quote_plus(raw_password)
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        f"postgresql+psycopg2://postgres:{safe_password}@localhost:5432/exchange_db"
    )

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

with app.app_context():
    print("Dropping all tables...")
    db.drop_all()
    print("All tables dropped successfully! Run app.py to recreate them.")

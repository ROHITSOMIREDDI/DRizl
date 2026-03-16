python -m venv venv
call venv\Scripts\activate.bat
pip install -r requirements.txt
python -c "from app import db, app; app.app_context().push(); db.create_all()"

from besthome_unified_bot import app as base_app, create_flask_app

app = base_app or create_flask_app()
